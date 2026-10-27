from django.contrib import admin
from .models import Profile, ActivityLog


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'language', 'birth_date', 'creation_date')
    list_filter = ('language',)
    search_fields = ('user__username', 'bio')


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'action', 'ai_service', 'status', 'duration_ms', 'creation_date')
    list_filter = ('status', 'ai_service')
    search_fields = ('user__username', 'action', 'ai_service')
