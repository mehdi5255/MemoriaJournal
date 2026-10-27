from django.contrib import admin
from .models import Photo, Person, FaceTag


@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'entry', 'taken_date', 'location', 'faces_analyzed')
    list_filter = ('faces_analyzed', 'taken_date')
    search_fields = ('caption', 'location', 'entry__title')


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'relation', 'birth_date')
    list_filter = ('relation',)
    search_fields = ('name', 'notes', 'user__username')


@admin.register(FaceTag)
class FaceTagAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'person', 'confidence', 'is_confirmed', 'creation_date')
    list_filter = ('is_confirmed',)
    search_fields = ('person__name', 'photo__caption')
