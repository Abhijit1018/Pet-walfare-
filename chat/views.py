from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse, HttpResponse
from .models import Conversation, Message, ChatParticipant, ChatMember
from webapp.models import Notification
from webapp.models import Pet
from django.shortcuts import HttpResponse
from django.contrib.auth.models import User
from django.views.decorators.http import require_POST


@login_required
@require_POST
def leave_conversation(request, convo_id):
    convo = get_object_or_404(Conversation, id=convo_id)
    chat_db = 'chat_db'
    # remove membership for current user
    ChatMember.objects.using(chat_db).filter(conversation_id=convo.id, user_id=request.user.id).delete()
    # if no members remain, delete conversation and messages
    remaining = ChatMember.objects.using(chat_db).filter(conversation_id=convo.id).exists()
    if not remaining:
        Message.objects.using(chat_db).filter(conversation_id=convo.id).delete()
        Conversation.objects.using(chat_db).filter(id=convo.id).delete()
    return redirect('chat:index')


@login_required
@require_POST
def delete_conversation(request, convo_id):
    convo = get_object_or_404(Conversation, id=convo_id)
    # only allow deletion by admins
    if not (request.user.is_staff or request.user.is_superuser):
        return HttpResponse('Forbidden', status=403)
    chat_db = 'chat_db'
    # Use raw SQL deletes on the chat DB to avoid ORM cascade logic that may
    # reference unmanaged/absent tables such as chat_conversation_participants.
    from django.db import connections, transaction, OperationalError
    conv_table = Conversation._meta.db_table
    msg_table = Message._meta.db_table
    member_table = ChatMember._meta.db_table
    with transaction.atomic(using=chat_db):
        with connections[chat_db].cursor() as cursor:
            # delete messages
            try:
                cursor.execute(f'DELETE FROM {msg_table} WHERE conversation_id = %s', [convo.id])
            except OperationalError:
                # table may not exist in this chat DB; ignore
                pass
            # delete members
            try:
                cursor.execute(f'DELETE FROM {member_table} WHERE conversation_id = %s', [convo.id])
            except OperationalError:
                pass
            # delete conversation row
            try:
                cursor.execute(f'DELETE FROM {conv_table} WHERE id = %s', [convo.id])
            except OperationalError:
                pass
    return redirect('chat:index')


@login_required
def chat_index(request):
    # List conversations for the user using the through table to avoid cross-db joins
    # Use ChatParticipant (integer user ids) instead of M2M through table
    convo_ids = list(ChatMember.objects.filter(user_id=request.user.id).values_list('conversation_id', flat=True))
    convos = list(Conversation.objects.filter(id__in=convo_ids))
    # collect participant ids for all conversations and load users from main DB
    convo_map = {c.id: c for c in convos}
    if convos:
        all_convo_ids = [c.id for c in convos]
        rows = ChatMember.objects.filter(conversation_id__in=all_convo_ids).values('conversation_id', 'user_id')
        convo_participants = {}
        user_ids = set()
        for r in rows:
            convo_participants.setdefault(r['conversation_id'], []).append(r['user_id'])
            user_ids.add(r['user_id'])
        from django.contrib.auth import get_user_model
        UserModel = get_user_model()
        users = UserModel.objects.filter(id__in=list(user_ids))
        users_map = {u.id: u for u in users}
        for cid, uids in convo_participants.items():
            # attach a safe participants list on the conversation object for templates
            setattr(convo_map[cid], 'participants_safe', [users_map.get(uid) for uid in uids if users_map.get(uid) is not None])
    return render(request, 'chat/index.html', {'conversations': convos})


@login_required
def conversation_view(request, convo_id):
    # Load conversation and ensure the current user is a participant (use through table to avoid cross-db joins)
    convo = get_object_or_404(Conversation, id=convo_id)
    if not ChatMember.objects.filter(conversation_id=convo.id, user_id=request.user.id).exists():
        from django.http import Http404
        raise Http404()
    if request.method == 'POST':
        text = request.POST.get('text', '').strip()
        if text:
            # Create message via the ORM on the chat DB (no FK to auth.User) to avoid
            # raw SQL, PRAGMA toggles and sqlite debugging-formatting issues.
            from django.utils import timezone
            chat_db = 'chat_db'
            msg = Message.objects.using(chat_db).create(
                conversation_id=convo.id,
                sender_id=request.user.id,
                text=text,
                created_at=timezone.now(),
                read=False
            )
            # Notify other participants - fetch participant ids from the ChatMember table to avoid cross-db joins
            participant_ids = list(ChatMember.objects.filter(conversation_id=convo.id).values_list('user_id', flat=True))
            # Exclude sender
            participant_ids = [pid for pid in participant_ids if pid != request.user.id]
            from django.contrib.auth import get_user_model
            UserModel = get_user_model()
            recipients = UserModel.objects.filter(id__in=participant_ids)
            for participant in recipients:
                Notification.objects.create(
                    user=participant,
                    actor=request.user,
                    verb='New message',
                    message=f'New message from {request.user.username}: {text[:200]}',
                    url=f'/chat/conversation/{convo.id}/'
                )
            return redirect('chat:conversation', convo_id=convo.id)
    # Load messages without select_related to avoid cross-db joins; then load senders from auth DB
    # use related_name 'chat_messages' for Message relation
    msgs = list(convo.chat_messages.all())
    sender_ids = {m.sender_id for m in msgs}
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    senders = UserModel.objects.filter(id__in=list(sender_ids)) if sender_ids else []
    senders_map = {s.id: s for s in senders}
    for m in msgs:
        setattr(m, 'sender_user', senders_map.get(m.sender_id))

    # attach conversation participants safely
    rows = ChatMember.objects.filter(conversation_id=convo.id).values_list('user_id', flat=True)
    if rows:
        users = UserModel.objects.filter(id__in=list(rows))
        users_map = {u.id: u for u in users}
    # attach participants attribute so templates don't try to access M2M across DBs
    setattr(convo, 'participants_safe', [users_map.get(uid) for uid in rows if users_map.get(uid) is not None])

    return render(request, 'chat/conversation.html', {'conversation': convo, 'messages': msgs})


@login_required
@require_POST
def send_message_ajax(request, convo_id):
    # Ensure user is participant without cross-db joins
    convo = get_object_or_404(Conversation, id=convo_id)
    if not ChatMember.objects.filter(conversation_id=convo.id, user_id=request.user.id).exists():
        return JsonResponse({'success': False, 'error': 'Not a participant'})
    text = request.POST.get('text', '').strip()
    if not text:
        return JsonResponse({'success': False, 'error': 'Empty message'})
    # Create the message via ORM on the chat DB
    from django.utils import timezone
    chat_db = 'chat_db'
    msg = Message.objects.using(chat_db).create(
        conversation_id=convo.id,
        sender_id=request.user.id,
        text=text,
        created_at=timezone.now(),
        read=False
    )
    # Notify other participants - avoid cross-db joins
    participant_ids = list(ChatMember.objects.filter(conversation_id=convo.id).values_list('user_id', flat=True))
    participant_ids = [pid for pid in participant_ids if pid != request.user.id]
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    recipients = UserModel.objects.filter(id__in=participant_ids)
    for participant in recipients:
        Notification.objects.create(
            user=participant,
            actor=request.user,
            verb='New message',
            message=f'New message from {request.user.username}: {msg.text[:200]}',
            url=f'/chat/conversation/{convo.id}/'
        )
    # sender is the current user (sender_id stored on Message)
    return JsonResponse({'success': True, 'id': msg.id, 'text': msg.text, 'sender': request.user.username, 'created_at': msg.created_at.isoformat()})


@login_required
def start_conversation(request, pet_id):
    """Create or return a conversation between the current user and the pet owner and redirect to it.
    This will ensure admin users are included as members of any 1-to-1 conversation.
    """
    pet = get_object_or_404(Pet, id=pet_id)
    owner = pet.owner
    if not owner:
        # No owner; direct to admin conversation or show message
        return HttpResponse('No owner is listed for this pet. Please contact admin.', status=400)
    if owner == request.user:
        return redirect('chat:index')

    # collect admin ids (staff or superuser), exclude current user if admin
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    admin_qs = UserModel.objects.filter(is_staff=True) | UserModel.objects.filter(is_superuser=True)
    admin_ids = list(admin_qs.values_list('id', flat=True))
    admin_ids = [aid for aid in admin_ids if aid != request.user.id]

    # We want conversations to be scoped to the specific pet so that starting a chat
    # about a different pet (even with the same participants) creates a NEW
    # conversation. To support that we only reuse an existing conversation if its
    # subject includes the pet id tag `(pet:<id>)`.
    from django.db.models import Count
    convo_ids = (ChatMember.objects
                .filter(user_id__in=[owner.id, request.user.id])
                .values('conversation_id')
                .annotate(cnt=Count('user_id'))
                .filter(cnt=2)
                .values_list('conversation_id', flat=True))
    # Look for a conversation among these ids that is explicitly about this pet
    pet_tag = f'(pet:{pet.id})'
    convo = Conversation.objects.filter(id__in=list(convo_ids), subject__contains=pet_tag).first()
    chat_db = 'chat_db'
    if convo:
        # ensure admins are members of existing conversation
        for aid in admin_ids:
            ChatMember.objects.using(chat_db).get_or_create(conversation_id=convo.id, user_id=aid)
        return redirect('chat:conversation', convo_id=convo.id)

    # create new conversation and add owner, requester and admins
    # create a conversation explicitly tagged with the pet id so it won't be
    # reused for other pets with the same participants
    convo = Conversation.objects.using(chat_db).create(subject=f'About {pet.name} {pet_tag}')
    # create chat members via get_or_create to avoid UNIQUE constraint failures
    ChatMember.objects.using(chat_db).get_or_create(conversation_id=convo.id, user_id=owner.id)
    ChatMember.objects.using(chat_db).get_or_create(conversation_id=convo.id, user_id=request.user.id)
    for aid in admin_ids:
        ChatMember.objects.using(chat_db).get_or_create(conversation_id=convo.id, user_id=aid)

    # Notify owner
    Notification.objects.create(
        user=owner,
        actor=request.user,
        verb='Conversation started',
        message=f'{request.user.username} started a conversation about {pet.name}.',
        url=f'/chat/conversation/{convo.id}/'
    )
    # Notify admins
    for aid in admin_ids:
        try:
            admin_user = UserModel.objects.get(id=aid)
            Notification.objects.create(
                user=admin_user,
                actor=request.user,
                verb='Conversation started',
                message=f"{request.user.username} started a conversation about {pet.name}.",
                url=f'/chat/conversation/{convo.id}/'
            )
        except UserModel.DoesNotExist:
            continue

    return redirect('chat:conversation', convo_id=convo.id)


@login_required
def start_with_admins(request):
    """Start or return a conversation that includes the current user and
    the selected admin user(s). If called with GET, render a selection
    page where the user can choose which admins to contact.
    """
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    from django.db.models import Q, Count

    # admins list
    admins = UserModel.objects.filter(Q(is_staff=True) | Q(is_superuser=True)).exclude(id=request.user.id)

    if request.method == 'GET':
        # Render selection page (if there are multiple admins, user can choose)
        return render(request, 'chat/select_admins.html', {'admins': admins})

    # POST: create/find conversation with chosen admin ids
    admin_ids = request.POST.getlist('admin_ids')
    if not admin_ids:
        return HttpResponse('No admin selected', status=400)
    # convert to ints and include current user
    try:
        admin_ids = [int(a) for a in admin_ids]
    except ValueError:
        return HttpResponse('Invalid admin ids', status=400)

    participant_ids = sorted(set(admin_ids + [request.user.id]))

    # Look for existing conversation that contains exactly these participants.
    # We find conversations where the count of ChatMember rows for these ids equals len(participant_ids)
    convo_ids = (ChatMember.objects
                .filter(user_id__in=participant_ids)
                .values('conversation_id')
                .annotate(cnt=Count('user_id'))
                .filter(cnt=len(participant_ids))
                .values_list('conversation_id', flat=True))
    convo = Conversation.objects.filter(id__in=list(convo_ids)).first()

    chat_db = 'chat_db'
    if not convo:
        convo = Conversation.objects.using(chat_db).create(subject='Support / Admins')

    # Ensure ChatMember rows exist; use get_or_create to avoid UNIQUE constraint errors
    for uid in participant_ids:
        ChatMember.objects.using(chat_db).get_or_create(conversation_id=convo.id, user_id=uid)

    # Notify selected admins only
    recipients = UserModel.objects.filter(id__in=admin_ids)
    for participant in recipients:
        Notification.objects.create(
            user=participant,
            actor=request.user,
            verb='Conversation started',
            message=f'{request.user.username} started a conversation with you.',
            url=f'/chat/conversation/{convo.id}/'
        )

    return redirect('chat:conversation', convo_id=convo.id)


@login_required
def fetch_messages(request, convo_id):
    convo = get_object_or_404(Conversation, id=convo_id)
    if not ChatMember.objects.filter(conversation_id=convo.id, user_id=request.user.id).exists():
        return JsonResponse({'messages': []})
    last_id = int(request.GET.get('after', 0))
    msgs = list(convo.chat_messages.filter(id__gt=last_id))
    sender_ids = {m.sender_id for m in msgs}
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    senders = UserModel.objects.filter(id__in=list(sender_ids)) if sender_ids else []
    senders_map = {s.id: s for s in senders}
    data = [{'id': m.id, 'sender': (senders_map.get(m.sender_id).username if senders_map.get(m.sender_id) else None), 'text': m.text, 'created_at': m.created_at.isoformat()} for m in msgs]
    return JsonResponse({'messages': data})


@login_required
@require_POST
def delete_message(request, convo_id, msg_id):
    """Admin-only: delete a single message from a conversation (operates on chat_db)."""
    if not (request.user.is_staff or request.user.is_superuser):
        return HttpResponse('Forbidden', status=403)
    # ensure conversation exists
    convo = get_object_or_404(Conversation, id=convo_id)
    chat_db = 'chat_db'
    # delete message by id using chat DB
    try:
        deleted, _ = Message.objects.using(chat_db).filter(id=msg_id, conversation_id=convo.id).delete()
    except Exception:
        deleted = 0
    if deleted:
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Message not found'})


def is_admin(user):
    return user.is_authenticated and (user.is_staff or hasattr(user, 'adminprofile'))


@user_passes_test(is_admin)
def admin_conversations(request):
    convos = list(Conversation.objects.all())
    convo_map = {c.id: c for c in convos}
    if convos:
        all_convo_ids = [c.id for c in convos]
        rows = ChatMember.objects.filter(conversation_id__in=all_convo_ids).values('conversation_id', 'user_id')
        convo_participants = {}
        user_ids = set()
        for r in rows:
            convo_participants.setdefault(r['conversation_id'], []).append(r['user_id'])
            user_ids.add(r['user_id'])
        from django.contrib.auth import get_user_model
        UserModel = get_user_model()
        users = UserModel.objects.filter(id__in=list(user_ids))
        users_map = {u.id: u for u in users}
        for cid, uids in convo_participants.items():
            setattr(convo_map[cid], 'participants_safe', [users_map.get(uid) for uid in uids if users_map.get(uid) is not None])
    return render(request, 'chat/admin_list.html', {'conversations': convos})
