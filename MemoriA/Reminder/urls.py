from django.urls import path
from . import views

app_name = 'reminder'

urlpatterns = [
    # Page « Rappels » (lien du menu)
    path('', views.ReminderListView.as_view(), name='reminder_list'),
    path('rappels/ajouter/', views.ReminderCreateView.as_view(), name='reminder_create'),
    path('rappels/<int:pk>/', views.ReminderDetailView.as_view(), name='reminder_detail'),
    path('rappels/<int:pk>/modifier/', views.ReminderUpdateView.as_view(), name='reminder_update'),
    path('rappels/<int:pk>/supprimer/', views.ReminderDeleteView.as_view(), name='reminder_delete'),
    path('rappels/<int:pk>/heure-conseillee/', views.ReminderSuggestView.as_view(), name='reminder_suggest'),
    path('rappels/<int:pk>/appliquer/', views.ReminderApplyView.as_view(), name='reminder_apply'),
    path('rappels/<int:pk>/question/', views.PromptGenerateView.as_view(), name='prompt_generate'),

    # Questions du jour
    path('questions/', views.PromptListView.as_view(), name='prompt_list'),
    path('questions/ajouter/', views.PromptCreateView.as_view(), name='prompt_create'),
    path('questions/<int:pk>/', views.PromptDetailView.as_view(), name='prompt_detail'),
    path('questions/<int:pk>/modifier/', views.PromptUpdateView.as_view(), name='prompt_update'),
    path('questions/<int:pk>/supprimer/', views.PromptDeleteView.as_view(), name='prompt_delete'),
    path('questions/<int:pk>/repondre/', views.PromptAnswerView.as_view(), name='prompt_answer'),
    path('questions/<int:pk>/ignorer/', views.PromptIgnoreView.as_view(), name='prompt_ignore'),
]
