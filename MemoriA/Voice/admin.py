from django.contrib import admin
from .models import VoiceNote, MoodRecord


@admin.register(VoiceNote)
class VoiceNoteAdmin(admin.ModelAdmin):
    list_display = ('title', 'entry', 'duration', 'language', 'status', 'creation_date')
    list_filter = ('status', 'language')
    search_fields = ('title', 'transcription', 'entry__title')


@admin.register(MoodRecord)
class MoodRecordAdmin(admin.ModelAdmin):
    list_display = ('emotion', 'user', 'source', 'score', 'record_date')
    list_filter = ('emotion', 'source', 'record_date')
    search_fields = ('user__username', 'note', 'entry__title')
