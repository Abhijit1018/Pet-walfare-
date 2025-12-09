from django.shortcuts import render, get_object_or_404
from .models import Pet, AdoptionRequest, UserProfile, AdminProfile, PetRegistrationRequest
from django.db.models import Count
from django.template import engines # Updated import for debugging
from django.template.exceptions import TemplateDoesNotExist
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect
from .forms import RegisterForm, PetForm, AdminRegistrationForm, CustomPasswordResetForm, CustomSetPasswordForm, PetRegistrationRequestForm
from django.contrib import messages
from django.contrib.auth.forms import AuthenticationForm
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils import timezone
from django.http import JsonResponse
from django.core.management import execute_from_command_line
from django.contrib.auth.views import PasswordResetView, PasswordResetConfirmView
from django.urls import reverse_lazy
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.models import User
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.core.mail import send_mail
from django.conf import settings
from django.views.decorators.http import require_http_methods
from .models import Notification
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import redirect

from chat.models import Conversation, ChatMember


def about(request):
    """About page for the site."""
    return render(request, 'webapp/about_modern.html', {'page_title': 'About'})


def contact(request):
    """Contact page with a simple form to message admins.

    On POST this will create a Notification for all admin users.
    """
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    admins = UserModel.objects.filter(is_staff=True) | UserModel.objects.filter(is_superuser=True)
    if request.method == 'POST':
        name = request.POST.get('name') or (request.user.get_full_name() if request.user.is_authenticated else 'Anonymous')
        email = request.POST.get('email') or (request.user.email if request.user.is_authenticated else '')
        message = request.POST.get('message', '').strip()
        if not message:
            return render(request, 'webapp/contact_modern.html', {'error': 'Please enter a message.', 'page_title': 'Contact', 'name': name, 'email': email})
        # Notify admins
        for admin in admins:
            Notification.objects.create(
                user=admin,
                actor=(request.user if request.user.is_authenticated else None),
                verb='Contact form message',
                message=f'Contact form from {name} ({email}): {message[:300]}',
                url=''
            )
        return render(request, 'webapp/contact_modern.html', {'success': True, 'page_title': 'Contact'})
    return render(request, 'webapp/contact_modern.html', {'page_title': 'Contact'})


def privacy(request):
    """Privacy policy page."""
    return render(request, 'webapp/privacy_modern.html', {'page_title': 'Privacy Policy'})


def terms(request):
    """Terms and conditions page."""
    return render(request, 'webapp/terms_modern.html', {'page_title': 'Terms and Conditions'})


def admin_start_chat(request, user_id):
    """Admin-only: start or return a conversation between admin team and the selected user."""
    from django.http import HttpResponseForbidden
    # Simple inline admin check to avoid decorator ordering issues
    if not (request.user.is_authenticated and (hasattr(request.user, 'adminprofile') or request.user.is_staff or request.user.is_superuser)):
        return HttpResponseForbidden('Admin access required')
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    target = get_object_or_404(UserModel, id=user_id)

    # Collect admin ids (staff or superuser) and ensure the requesting admin is included
    admin_qs = UserModel.objects.filter(is_staff=True) | UserModel.objects.filter(is_superuser=True)
    admin_ids = list(admin_qs.values_list('id', flat=True))
    # Ensure current admin included
    if request.user.id not in admin_ids:
        admin_ids.append(request.user.id)

    # Build participant list: target user + admins
    participant_ids = sorted(set([target.id] + admin_ids))

    # Always create a fresh admin conversation so admins can start a new DM
    # with the user even if previous admin chats exist.
    chat_db = 'chat_db'
    # include a timestamp suffix so subject is unique and easy to search
    convo = Conversation.objects.using(chat_db).create(subject=f'Admin chat with {target.username} - {timezone.now().isoformat()}')

    # Ensure ChatMember rows exist for all participants (use get_or_create to avoid UNIQUE errors)
    for uid in participant_ids:
        ChatMember.objects.using(chat_db).get_or_create(conversation_id=convo.id, user_id=uid)

    return redirect('chat:conversation', convo_id=convo.id)

def adoption_list(request):
    # Auto-move found pets to adoption after 15 days
    found_pets = Pet.objects.filter(status='found', found_date__isnull=False)
    moved_count = 0
    for pet in found_pets:
        if pet.auto_move_to_adoption():
            moved_count += 1
    
    if moved_count > 0:
        messages.info(request, f'{moved_count} found pet(s) have been automatically moved to adoption after 15 days.')
    
    # Fetch only pets that are 'for_adoption'
    pets = Pet.objects.filter(status='for_adoption').order_by('-date_added')
    context = {
        'pets': pets,
        'page_title': 'Pets Available for Adoption'
    }
    # Render the pet_list.html template with the filtered pets
    return render(request, 'webapp/pet_list_modern.html', context)


def home(request):
    """Home view that shows all pets regardless of status."""
    pets = Pet.objects.select_related('owner').order_by('-date_added')
    context = {
        'pets': pets,
        'page_title': 'All Pets'
    }
    return render(request, 'webapp/pet_list_modern.html', context)

# View for the Lost Pets page
def lost_list(request):
    # Fetch only pets that are 'lost'
    pets = Pet.objects.filter(status='lost').order_by('-date_added')
    context = {
        'pets': pets,
        'page_title': 'Lost Pets'
    }
    # Reuse the same template
    return render(request, 'webapp/pet_list_modern.html', context)

# View for the Found Pets page
def found_list(request):
    # Fetch only pets that are 'found'
    pets = Pet.objects.filter(status='found').order_by('-date_added')
    
    # Add days remaining info for each pet
    for pet in pets:
        if pet.found_date:
            days_passed = (timezone.now() - pet.found_date).days
            pet.days_remaining = max(0, 15 - days_passed)
        else:
            pet.days_remaining = 15
    
    context = {
        'pets': pets,
        'page_title': 'Found Pets'
    }
    # Reuse the same template
    return render(request, 'webapp/pet_list_modern.html', context)

# View for a single pet's detail page
def pet_detail(request, pet_id):
    pet = get_object_or_404(Pet, id=pet_id)
    
    # Find similar pets (same species, different ID, same status)
    similar_pets = Pet.objects.filter(
        species=pet.species,
        status=pet.status
    ).exclude(id=pet.id)[:3]
    
    context = {
        'pet': pet,
        'similar_pets': similar_pets
    }

    # Add days remaining info for found pets
    if pet.status == 'found' and pet.found_date:
        days_passed = (timezone.now() - pet.found_date).days
        context['days_remaining'] = max(0, 15 - days_passed)
    
    return render(request, 'webapp/pet_detail_modern.html', context)

def register_view(request):
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Registration successful. Please log in.')
            return redirect('webapp:login')
    else:
        form = RegisterForm()
    return render(request, 'webapp/register_modern.html', {'form': form})

def login_view(request):
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, f'Welcome, {user.username}!')
            return redirect('webapp:dashboard')
        else:
            messages.error(request, 'Invalid username or password.')
    else:
        form = AuthenticationForm()
    return render(request, 'webapp/login_modern.html', {'form': form})

def logout_view(request):
    logout(request)
    messages.info(request, 'You have been logged out.')
    return redirect('webapp:login')

@login_required
def add_pet(request):
    if request.method == 'POST':
        form = PetForm(request.POST, request.FILES)
        if form.is_valid():
            pet = form.save(commit=False)
            pet.owner = request.user
            
            # Set found_date if status is 'found'
            if pet.status == 'found':
                pet.found_date = timezone.now()
            
            pet.save()
            messages.success(request, 'Pet added successfully!')
            return redirect('webapp:dashboard')
    else:
        form = PetForm()
    return render(request, 'webapp/add_pet_modern.html', {'form': form})


@login_required
def edit_pet(request, pet_id):
    """Allow a pet owner (or admin) to edit their pet listing."""
    pet = get_object_or_404(Pet, id=pet_id)

    # Only owner or admin can edit
    if pet.owner != request.user and not (hasattr(request.user, 'adminprofile') or request.user.is_staff):
        messages.error(request, 'You do not have permission to edit this pet.')
        return redirect('webapp:dashboard')

    if request.method == 'POST':
        form = PetForm(request.POST, request.FILES, instance=pet)
        if form.is_valid():
            pet = form.save(commit=False)
            # If changing status to found, set found_date if needed
            if pet.status == 'found' and not pet.found_date:
                pet.found_date = timezone.now()
            pet.save()
            messages.success(request, 'Pet updated successfully!')
            return redirect('webapp:dashboard')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = PetForm(instance=pet)

    return render(request, 'webapp/edit_pet_modern.html', {'form': form, 'pet': pet, 'editing': True})

# Update dashboard to show user's pets

def dashboard(request):
    if not request.user.is_authenticated:
        return redirect('webapp:login')
        
    # User's pets
    user_pets = Pet.objects.filter(owner=request.user)
    
    # User's own adoption requests (sent by user)
    sent_requests = AdoptionRequest.objects.filter(user=request.user).select_related('pet')
    
    # Incoming requests for user's pets (received by user)
    received_requests = AdoptionRequest.objects.filter(
        pet__owner=request.user
    ).exclude(user=request.user).select_related('pet', 'user')
    
    # Approved requests
    approved_requests = received_requests.filter(status='approved')
    
    return render(request, 'webapp/dashboard_modern.html', {
        'user_pets': user_pets,
        'sent_requests': sent_requests,
        'received_requests': received_requests,
        'approved_requests': approved_requests,
    })

def request_adoption(request, pet_id):
    pet = get_object_or_404(Pet, id=pet_id)
    if not request.user.is_authenticated:
        messages.error(request, 'You must be logged in to request adoption.')
        return redirect('webapp:login')
    if pet.owner == request.user:
        messages.info(request, 'You are the owner of this pet.')
        return redirect('webapp:pet_detail', pet_id=pet.id)
    if AdoptionRequest.objects.filter(pet=pet, user=request.user, status='pending').exists():
        messages.info(request, 'You have already requested adoption for this pet.')
        return redirect('webapp:pet_detail', pet_id=pet.id)
    if request.method == 'POST':
        message = request.POST.get('message', '')
        AdoptionRequest.objects.create(pet=pet, user=request.user, message=message)
        # Notify pet owner (if set)
        if pet.owner and pet.owner != request.user:
            Notification.objects.create(
                user=pet.owner,
                actor=request.user,
                verb='New adoption request',
                message=f'{request.user.username} has requested to adopt {pet.name}.',
                url=f"/pet/{pet.id}/"
            )
        messages.success(request, 'Adoption request submitted!')
        return redirect('webapp:pet_detail', pet_id=pet.id)
    return render(request, 'webapp/request_adoption.html', {'pet': pet})

@login_required
def manage_adoption_request(request, request_id, action):
    adoption_request = get_object_or_404(AdoptionRequest, id=request_id, pet__owner=request.user)
    if action == 'approve':
        adoption_request.status = 'approved'
        adoption_request.pet.status = 'adopted'
        adoption_request.pet.save()
        adoption_request.save()
        # Notify requester about approval
        Notification.objects.create(
            user=adoption_request.user,
            actor=request.user,
            verb='Adoption request approved',
            message=f'Your adoption request for {adoption_request.pet.name} was approved.',
            url=f"/pet/{adoption_request.pet.id}/"
        )
        messages.success(request, 'Adoption request approved!')
    elif action == 'reject':
        adoption_request.status = 'rejected'
        adoption_request.save()
        # Notify requester about rejection
        Notification.objects.create(
            user=adoption_request.user,
            actor=request.user,
            verb='Adoption request rejected',
            message=f'Your adoption request for {adoption_request.pet.name} was rejected.',
            url=f"/pet/{adoption_request.pet.id}/"
        )
        messages.info(request, 'Adoption request rejected.')
    return redirect('webapp:dashboard')

def admin_register_view(request):
    if request.method == 'POST':
        form = AdminRegistrationForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Admin account created successfully. Please log in.')
            return redirect('webapp:login')
    else:
        form = AdminRegistrationForm()
    return render(request, 'webapp/admin_register_modern.html', {'form': form})

def is_admin(user):
    return user.is_authenticated and hasattr(user, 'adminprofile')

@user_passes_test(is_admin)
def admin_dashboard(request):
    # Check if user is admin
    if not (request.user.is_authenticated and hasattr(request.user, 'adminprofile')):
        messages.error(request, 'Access denied. Admin privileges required.')
        return redirect('webapp:dashboard')
    
    # Statistics
    total_pets = Pet.objects.count()
    adoption_pets = Pet.objects.filter(status='for_adoption').count()
    lost_pets = Pet.objects.filter(status='lost').count()
    found_pets = Pet.objects.filter(status='found').count()
    total_users = User.objects.count()
    pending_requests = AdoptionRequest.objects.filter(status='pending').count()
    pending_registration_requests = PetRegistrationRequest.objects.filter(status='pending').count()
    
    # Recent activity
    recent_pets = Pet.objects.select_related('owner').order_by('-date_added')[:10]
    
    context = {
        'total_pets': total_pets,
        'adoption_pets': adoption_pets,
        'lost_pets': lost_pets,
        'found_pets': found_pets,
        'total_users': total_users,
        'pending_requests': pending_requests,
        'pending_registration_requests': pending_registration_requests,
        'recent_pets': recent_pets,
    }
    return render(request, 'webapp/admin_dashboard_modern.html', context)

@user_passes_test(is_admin)
def admin_pet_management(request):
    # Check if user is admin
    if not (request.user.is_authenticated and hasattr(request.user, 'adminprofile')):
        messages.error(request, 'Access denied. Admin privileges required.')
        return redirect('webapp:dashboard')
    
    all_pets = Pet.objects.select_related('owner').order_by('-date_added')
    return render(request, 'webapp/admin_pets_modern.html', {'all_pets': all_pets})

@user_passes_test(is_admin)
def admin_user_management(request):
    # Get all user profiles with related user data
    all_users = UserProfile.objects.select_related('user').all()
    
    # Count admin users
    admin_count = AdminProfile.objects.count()
    
    # Count users with pets
    active_owners = UserProfile.objects.annotate(
        pet_count=Count('user__pet')
    ).filter(pet_count__gt=0).count()
    
    # Count recent signups (last 30 days)
    from datetime import datetime, timedelta
    recent_cutoff = datetime.now() - timedelta(days=30)
    recent_signups = UserProfile.objects.filter(
        user__date_joined__gte=recent_cutoff
    ).count()
    
    # Annotate each user with their pet count
    all_users = all_users.annotate(pet_count=Count('user__pet'))
    
    context = {
        'all_users': all_users,
        'admin_count': admin_count,
        'active_owners': active_owners,
        'recent_signups': recent_signups,
    }
    
    return render(request, 'webapp/admin_users_modern.html', context)

@user_passes_test(is_admin)
def toggle_pet_status(request, pet_id):
    if request.method == 'POST':
        pet = get_object_or_404(Pet, id=pet_id)
        new_status = request.POST.get('status')
        if new_status in dict(Pet.STATUS_CHOICES):
            old_status = pet.status
            pet.status = new_status
            
            # Set found_date if changing to 'found'
            if new_status == 'found' and old_status != 'found':
                pet.found_date = timezone.now()
            
            pet.save()
            messages.success(request, f'Pet status updated from {old_status} to {new_status}')
        return JsonResponse({'success': True})
    return JsonResponse({'success': False})

# Password Reset Views
class CustomPasswordResetViewClass(PasswordResetView):
    form_class = CustomPasswordResetForm
    template_name = 'webapp/password_reset.html'
    email_template_name = 'webapp/password_reset_email.html'
    success_url = reverse_lazy('webapp:password_reset_done')
    
    def form_valid(self, form):
        messages.success(self.request, 'Password reset email sent successfully!')
        return super().form_valid(form)

def password_reset_request(request):
    if request.method == 'POST':
        form = CustomPasswordResetForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            try:
                user = User.objects.get(email=email)
                # Generate token and uid
                token = default_token_generator.make_token(user)
                uid = urlsafe_base64_encode(force_bytes(user.pk))
                
                # Create reset link (you would normally use domain)
                reset_link = request.build_absolute_uri(f'/password-reset-confirm/{uid}/{token}/')
                
                # Send email (for development, we'll just show a message)
                try:
                    send_mail(
                        'Password Reset Request',
                        f'Click the link to reset your password: {reset_link}',
                        settings.DEFAULT_FROM_EMAIL,
                        [email],
                        fail_silently=False,
                    )
                    messages.success(request, 'Password reset email sent successfully!')
                except Exception as e:
                    # For development purposes, show the reset link in messages
                    messages.success(request, f'Password reset link (email not configured): {reset_link}')
                
                return redirect('webapp:password_reset_done')
            except User.DoesNotExist:
                messages.error(request, 'No user found with this email address.')
    else:
        form = CustomPasswordResetForm()
    
    return render(request, 'webapp/password_reset.html', {'form': form})

def password_reset_done(request):
    return render(request, 'webapp/password_reset_done.html')

class CustomPasswordResetConfirmViewClass(PasswordResetConfirmView):
    form_class = CustomSetPasswordForm
    template_name = 'webapp/password_reset_confirm.html'
    success_url = reverse_lazy('webapp:password_reset_complete')
    
    def form_valid(self, form):
        messages.success(self.request, 'Your password has been reset successfully!')
        return super().form_valid(form)

def password_reset_complete(request):
    return render(request, 'webapp/password_reset_complete.html')


@login_required
def notifications_list(request):
    notifications = Notification.objects.filter(user=request.user).order_by('-created_at')[:50]
    return render(request, 'webapp/notifications.html', {'notifications': notifications})


@login_required
@require_http_methods(["POST"])
def mark_notification_read(request, notification_id):
    notif = get_object_or_404(Notification, id=notification_id, user=request.user)
    notif.unread = False
    notif.save()
    return JsonResponse({'success': True})

@login_required
def redirect_to_register_pet(request):
    """Redirect old add-pet URL to new registration system"""
    messages.info(request, 'Pet registration now requires admin approval. Please use the new registration system.')
    return redirect('webapp:register_pet_request')

@login_required
def register_pet_request(request):
    """View for users to submit pet registration requests"""
    if request.method == 'POST':
        form = PetRegistrationRequestForm(request.POST, request.FILES)
        if form.is_valid():
            registration = form.save(commit=False)
            registration.user = request.user
            registration.save()
            messages.success(request, 'Your pet registration request has been submitted! An admin will review it shortly.')
            return redirect('webapp:registration_status')
    else:
        form = PetRegistrationRequestForm()
    
    return render(request, 'webapp/register_pet_request.html', {'form': form})

@login_required
def registration_status(request):
    """View for users to check their registration request status"""
    registrations = PetRegistrationRequest.objects.filter(user=request.user).order_by('-created_at')
    return render(request, 'webapp/registration_status.html', {'registrations': registrations})

def is_admin(user):
    """Helper function to check if user is an admin"""
    return user.is_authenticated and (user.is_staff or AdminProfile.objects.filter(user=user).exists())

@user_passes_test(is_admin)
def admin_registration_requests(request):
    """Admin view to see all pending registration requests"""
    pending_requests = PetRegistrationRequest.objects.filter(status='pending').order_by('-created_at')
    all_requests = PetRegistrationRequest.objects.all().order_by('-created_at')[:50]  # Last 50 requests
    
    context = {
        'pending_requests': pending_requests,
        'all_requests': all_requests,
    }
    return render(request, 'webapp/admin_registration_requests.html', context)


@user_passes_test(is_admin)
@require_POST
def run_auto_move_command(request):
    """Admin endpoint to trigger the auto-move logic and return JSON."""
    moved = 0
    found_pets = Pet.objects.filter(status='found', found_date__isnull=False)
    for pet in found_pets:
        if pet.auto_move_to_adoption():
            moved += 1

    return JsonResponse({'success': True, 'moved': moved})

@user_passes_test(is_admin)
@require_POST
def approve_registration_request(request, request_id):
    """Admin view to approve a registration request"""
    registration = get_object_or_404(PetRegistrationRequest, id=request_id, status='pending')
    admin_notes = request.POST.get('admin_notes', '')
    
    try:
        pet = registration.approve(request.user, admin_notes)
        if pet:
            messages.success(request, f'Registration approved! Pet "{pet.name}" has been created and is now visible to users.')
            # Notify requester
            Notification.objects.create(
                user=registration.user,
                actor=request.user,
                verb='Registration approved',
                message=f'Your pet registration for "{registration.name}" was approved.',
                url=f"/pet/{pet.id}/"
            )
        else:
            messages.error(request, 'Failed to approve registration.')
    except Exception as e:
        messages.error(request, f'Error approving registration: {str(e)}')
    
    return redirect('webapp:admin_registration_requests')

@user_passes_test(is_admin)
@require_POST
def reject_registration_request(request, request_id):
    """Admin view to reject a registration request"""
    registration = get_object_or_404(PetRegistrationRequest, id=request_id, status='pending')
    admin_notes = request.POST.get('admin_notes', '')
    
    try:
        registration.reject(request.user, admin_notes)
        messages.success(request, f'Registration for "{registration.name}" has been rejected.')
        # Notify requester
        Notification.objects.create(
            user=registration.user,
            actor=request.user,
            verb='Registration rejected',
            message=f'Your pet registration for "{registration.name}" was rejected.',
            url=''
        )
    except Exception as e:
        messages.error(request, f'Error rejecting registration: {str(e)}')
    
    return redirect('webapp:admin_registration_requests')

