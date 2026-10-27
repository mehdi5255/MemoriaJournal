from django.contrib import admin
from .models import Story, GeneratedImage, Retrospective


@admin.register(Story)
class StoryAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'style', 'version', 'update_date')
    list_filter = ('style',)
    search_fields = ('text', 'entry__title')


@admin.register(GeneratedImage)
class GeneratedImageAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'story', 'style', 'is_main', 'creation_date')
    list_filter = ('style', 'is_main')
    search_fields = ('prompt', 'story__entry__title')


@admin.register(Retrospective)
class RetrospectiveAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'period_type', 'start_date', 'end_date')
    list_filter = ('period_type',)
    search_fields = ('title', 'summary', 'user__username')
    filter_horizontal = ('entries',)
