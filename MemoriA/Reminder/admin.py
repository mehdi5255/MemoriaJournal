from django.contrib import admin
from .models import Reminder, DailyPrompt


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ('user', 'reminder_time', 'days', 'channel', 'suggested_time', 'is_active')
    list_filter = ('channel', 'is_active')
    search_fields = ('user__username', 'message')


@admin.register(DailyPrompt)
class DailyPromptAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'user', 'status', 'prompt_date')
    list_filter = ('status', 'prompt_date')
    search_fields = ('question', 'user__username')
