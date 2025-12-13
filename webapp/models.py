from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from cloudinary.models import CloudinaryField

class UserProfile(models.Model):
    GENDER_CHOICES = [
        ('male', 'Male'),
        ('female', 'Female'),
        ('other', 'Other'),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    age = models.PositiveIntegerField(null=True, blank=True)
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True, null=True)
    location = models.CharField(max_length=100, blank=True, null=True)
    
    def __str__(self):
        return f"{self.user.username}'s Profile"

class AdminProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    is_super_admin = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Admin: {self.user.username}"
    
    @classmethod
    def can_create_admin(cls):
        return cls.objects.count() < 3

class Pet(models.Model):
    # --- Choices for the status field ---
    STATUS_CHOICES = [
        ('for_adoption', 'For Adoption'),
        ('lost', 'Lost'),
        ('found', 'Found'),
        ('adopted', 'Adopted/Reunited'),
    ]

    SPECIES_CHOICES = [
        ('dog', 'Dog'),
        ('cat', 'Cat'),
        ('rabbit', 'Rabbit'),
        ('bird', 'Bird'),
        ('other', 'Other'),
    ]
    
    GENDER_CHOICES = [
        ('male', 'Male'),
        ('female', 'Female'),
        ('unknown', 'Unknown'),
    ]

    # --- Model Fields ---
    name = models.CharField(max_length=50)
    species = models.CharField(max_length=50, choices=SPECIES_CHOICES)
    breed = models.CharField(max_length=50, blank=True, null=True)
    color = models.CharField(max_length=50, blank=True, null=True)
    age = models.CharField(max_length=20, blank=True, null=True, help_text="e.g., '2 years', '6 months', 'Puppy'")
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, default='unknown')
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='for_adoption')
    location = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    contact_email = models.EmailField(blank=True, null=True)
    contact_phone = models.CharField(max_length=15, blank=True, null=True)
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    image = CloudinaryField('image', blank=True, null=True)
    date_added = models.DateTimeField(auto_now_add=True)
    found_date = models.DateTimeField(null=True, blank=True, help_text="Date when the pet was found")
    
    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"
    
    def should_move_to_adoption(self):
        """Check if found pet should be moved to adoption after 15 days"""
        if self.status == 'found' and self.found_date:
            return timezone.now() >= self.found_date + timedelta(days=15)
        return False
    
    def auto_move_to_adoption(self):
        """Automatically move found pets to adoption after 15 days"""
        if self.should_move_to_adoption():
            self.status = 'for_adoption'
            self.save()
            return True
        return False

class PetRegistrationRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    
    SPECIES_CHOICES = [
        ('dog', 'Dog'),
        ('cat', 'Cat'),
        ('rabbit', 'Rabbit'),
        ('bird', 'Bird'),
        ('other', 'Other'),
    ]
    
    PET_STATUS_CHOICES = [
        ('for_adoption', 'For Adoption'),
        ('lost', 'Lost'),
        ('found', 'Found'),
    ]
    
    GENDER_CHOICES = [
        ('male', 'Male'),
        ('female', 'Female'),
        ('unknown', 'Unknown'),
    ]
    
    # User who submitted the request
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pet_registration_requests')
    
    # Pet details from the registration form
    name = models.CharField(max_length=50)
    species = models.CharField(max_length=50, choices=SPECIES_CHOICES)
    breed = models.CharField(max_length=50, blank=True, null=True)
    color = models.CharField(max_length=50, blank=True, null=True)
    age = models.CharField(max_length=20, blank=True, null=True, help_text="e.g., '2 years', '6 months', 'Puppy'")
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, default='unknown')
    pet_status = models.CharField(max_length=15, choices=PET_STATUS_CHOICES, default='for_adoption')
    location = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    contact_email = models.EmailField(blank=True, null=True)
    contact_phone = models.CharField(max_length=15, blank=True, null=True)
    image = CloudinaryField('image', blank=True, null=True)
    
    # Request management fields
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    admin_notes = models.TextField(blank=True, null=True, help_text="Admin notes for approval/rejection")
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_registrations')
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    
    # Reference to created pet (after approval)
    created_pet = models.ForeignKey(Pet, on_delete=models.SET_NULL, null=True, blank=True, related_name='registration_request')
    
    def __str__(self):
        return f"{self.user.username} - {self.name} ({self.get_status_display()})"
    
    def approve(self, admin_user, admin_notes=""):
        """Approve the registration and create the pet"""
        if self.status == 'pending':
            # Create the actual Pet instance
            pet = Pet.objects.create(
                name=self.name,
                species=self.species,
                breed=self.breed,
                color=self.color,
                age=self.age,
                gender=self.gender,
                status=self.pet_status,
                location=self.location,
                description=self.description,
                contact_email=self.contact_email or self.user.email,
                contact_phone=self.contact_phone,
                owner=self.user,
                image=self.image
            )
            
            # Update registration request
            self.status = 'approved'
            self.admin_notes = admin_notes
            self.reviewed_by = admin_user
            self.reviewed_at = timezone.now()
            self.created_pet = pet
            self.save()
            
            return pet
        return None
    
    def reject(self, admin_user, admin_notes=""):
        """Reject the registration request"""
        if self.status == 'pending':
            self.status = 'rejected'
            self.admin_notes = admin_notes
            self.reviewed_by = admin_user
            self.reviewed_at = timezone.now()
            self.save()

class AdoptionRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    pet = models.ForeignKey(Pet, on_delete=models.CASCADE, related_name='adoption_requests')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} requests {self.pet.name} ({self.status})"


class Notification(models.Model):
    """Simple notification model for in-app alerts."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='notifications_sent')
    verb = models.CharField(max_length=255)
    message = models.TextField(blank=True, null=True)
    url = models.CharField(max_length=255, blank=True, null=True, help_text='Optional URL to link to in the app')
    unread = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Notification to {self.user.username}: {self.verb}"

