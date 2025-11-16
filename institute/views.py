from django.contrib.auth import get_user_model
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from .models import Profile, Institute, Role

User = get_user_model()

@login_required
def create_profile_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        role = request.POST.get('role')
        
        try:
            user_institute = request.user.profile.institute
        except AttributeError:
            messages.error(request, "You must belong to an institute to create profiles.")
            return redirect('create_profile')  
        
        if role not in [choice[0] for choice in Role.choices]:
            messages.error(request, "Invalid role selected.")
            return render(request, 'your_template.html')  
        
        # Check if user already exists
        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists.")
            return render(request, 'your_template.html')
        
        if User.objects.filter(email=email).exists():
            messages.error(request, "Email already exists.")
            return render(request, 'your_template.html')
    
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password
        )
        
        profile = Profile.objects.create(
            user=user,
            role=role,
            institute=user_institute
        )
        
        messages.success(request, f"Profile for {username} created successfully.")
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': f'Profile for {username} created successfully.',
                'new_profile': {
                    'username': user.username,
                    'email': user.email,
                    'role': role
                }
            })
        
        return redirect('create_profile') 
    
    try:
        user_institute = request.user.profile.institute
        institute_code = user_institute.code
        recent_profiles = Profile.objects.filter(institute=user_institute).order_by('-created_at')[:10]

    except AttributeError:
        user_institute = None
        institute_code = None
        
    
    context = {
        'institute': user_institute,
        'code': institute_code,
        'recent_profiles':recent_profiles
    }
    return render(request, 'institute/create_profile.html', context)  