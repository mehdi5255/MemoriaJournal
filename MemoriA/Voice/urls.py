from django.urls import path
from . import views

app_name = 'voice'

urlpatterns = [
    # Page Humeur (lien « Voix et humeur » du menu)
    path('', views.MoodListView.as_view(), name='mood_list'),

    # Dictée depuis le formulaire d'une entrée (JavaScript)
    path('dictee/', views.DictationView.as_view(), name='dictation'),

    # Notes vocales
    path('notes/', views.VoiceNoteListView.as_view(), name='note_list'),
    path('notes/ajouter/', views.VoiceNoteCreateView.as_view(), name='note_create'),
    path('notes/<int:pk>/', views.VoiceNoteDetailView.as_view(), name='note_detail'),
    path('notes/<int:pk>/modifier/', views.VoiceNoteUpdateView.as_view(), name='note_update'),
    path('notes/<int:pk>/supprimer/', views.VoiceNoteDeleteView.as_view(), name='note_delete'),
    path('notes/<int:pk>/transcrire/', views.VoiceNoteTranscribeView.as_view(), name='note_transcribe'),

    # Humeurs
    path('humeurs/ajouter/', views.MoodCreateView.as_view(), name='mood_create'),
    path('humeurs/detecter/', views.MoodDetectView.as_view(), name='mood_detect'),
    path('humeurs/<int:pk>/', views.MoodDetailView.as_view(), name='mood_detail'),
    path('humeurs/<int:pk>/modifier/', views.MoodUpdateView.as_view(), name='mood_update'),
    path('humeurs/<int:pk>/supprimer/', views.MoodDeleteView.as_view(), name='mood_delete'),
]
