from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from classroom.models import Class, Enrollment
from institute.models import Profile, Role
from django.utils import timezone


def join_class(request):
    """
    Student submits a class code to request enrollment.
    Creates a pending Enrollment (approved=False).
    """
    try:
        profile = request.user.profile
        if profile.role != Role.STUDENT:
            messages.error(request, "Only students can join classes.")
            return redirect("home")
    except Profile.DoesNotExist:
        messages.error(request, "Your profile is incomplete.")
        return redirect("home")

    institute = profile.institute

    if request.method == "POST":
        class_code = request.POST.get("class_code", "").strip()
        print(class_code)
        if not class_code:
            messages.error(request, "Please enter a class code.")
        else:
            try:
                target_class = Class.objects.get(class_code=class_code, institute=institute)

                enrollment, created = Enrollment.objects.get_or_create(
                    student=request.user,
                    class_in=target_class,
                    defaults={"approved": False}
                )

                if not created:
                    if enrollment.approved:
                        messages.info(request, "You are already enrolled in this class.")
                    else:
                        messages.info(request, "You have already requested to join this class. Awaiting teacher approval.")
                else:
                    messages.success(request, f"Request sent to join '{target_class.name}'. Awaiting teacher approval.")

            except Class.DoesNotExist:
                messages.error(request, "Invalid class code or class not in your institute.")

        return redirect("join_class")

    enrollments = request.user.enrollments.select_related("class_in__teacher").all()
    return render(request, "teacher/join_class.html", {
        "institute": institute,
        "student_enrollments": enrollments,
    })


def manage_enrollment_requests(request):
    """
    Teacher view to see and approve student enrollment requests for their classes.
    """
    try:
        profile = request.user.profile
        if profile.role != Role.TEACHER:
            messages.error(request, "Only teachers can manage enrollments.")
            return redirect("home")
    except Profile.DoesNotExist:
        messages.error(request, "Your profile is incomplete.")
        return redirect("home")

    # Only enrollments for classes taught by this teacher
    enrollments = Enrollment.objects.filter(
        class_in__teacher=request.user
    ).select_related('student', 'class_in').order_by('-joined_at')

    if request.method == "POST":
        enrollment_id = request.POST.get("enrollment_id")
        action = request.POST.get("action")

        if action == "approve" and enrollment_id:
            enrollment = get_object_or_404(Enrollment, id=enrollment_id, class_in__teacher=request.user)
            if not enrollment.approved:
                enrollment.approved = True
                enrollment.save()
                messages.success(request, f"Approved {enrollment.student.username} for {enrollment.class_in.name}.")
            else:
                messages.info(request, "This enrollment is already approved.")

        return redirect("manage_enrollment_requests")

    # Summary stats
    teacher_classes = Class.objects.filter(teacher=request.user)
    total_classes = teacher_classes.count()

    approved_enrollments = Enrollment.objects.filter(
        class_in__teacher=request.user,
        approved=True
    )
    total_approved_students = approved_enrollments.count()

    pending_requests = enrollments.filter(approved=False)
    total_pending_requests = pending_requests.count()

    # Approved today (optional enhancement)
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    approved_today = approved_enrollments.filter(joined_at__gte=today_start).count()

    context = {
        "all_enrollment_requests": enrollments,
        "total_classes": total_classes,
        "total_approved_students": total_approved_students,
        "total_pending_requests": total_pending_requests,
        "approved_today": approved_today,
    }

    return render(request, "teacher/approve_class.html", context)