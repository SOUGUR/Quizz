from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import Class, Role
from institute.models import Profile
from django.db import transaction


def create_classroom(request):
    try:
        profile = request.user.profile
        if profile.role != Role.TEACHER:
            messages.error(request, "Only teachers can create classrooms.")
            return redirect("home") 
    except Profile.DoesNotExist:
        messages.error(request, "Your account profile is incomplete.")
        return redirect("home")

    institute = profile.institute

    if request.method == "POST":
        created_count = 0
        duplicate_names = []

        class_names = []
        index = 0
        while True:
            name = request.POST.get(f"class_{index}_name", "").strip()
            if not name:
                break
            if name in class_names:
                duplicate_names.append(name)
            else:
                class_names.append(name)
            index += 1
            if index >= 5:  
                break

        if duplicate_names:
            messages.warning(request, f"Duplicate class names ignored: {', '.join(set(duplicate_names))}")

        if not class_names:
            messages.error(request, "Please provide at least one class name.")
            return render(request, "classroom/create_classroom.html", {
                "institute": institute,
                "classes_taught": request.user.classes_taught.all()
            })

        try:
            with transaction.atomic():
                for name in class_names:
                    Class.objects.create(
                        institute=institute,
                        name=name,
                        teacher=request.user
                    )
                    created_count += 1
            messages.success(request, f"Successfully created {created_count} class{'es' if created_count > 1 else ''}!")
        except Exception as e:
            messages.error(request, "An error occurred while creating classes. Please try again.")

        return redirect("create_classroom")  
    # GET request
    return render(request, "classroom/create_classroom.html", {
        "institute": institute,
        "classes_taught": request.user.classes_taught.all()
    })