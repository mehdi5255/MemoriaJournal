from django.contrib import admin
from .models import Tag, JournalEntry


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'color', 'creation_date')
    list_filter = ('user',)
    search_fields = ('name', 'user__username')


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'entry_date', 'location', 'is_favorite')
    list_filter = ('is_favorite', 'entry_date', 'tags')
    search_fields = ('title', 'content', 'location', 'user__username')
    filter_horizontal = ('tags',)
