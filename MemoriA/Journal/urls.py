from django.urls import path

from . import views

app_name = 'journal'

urlpatterns = [
    # Entrées du journal
    path('', views.EntryListView.as_view(), name='entry_list'),
    path('ajouter/', views.EntryCreateView.as_view(), name='entry_create'),
    path('<int:pk>/', views.EntryDetailView.as_view(), name='entry_detail'),
    path('<int:pk>/modifier/', views.EntryUpdateView.as_view(), name='entry_update'),
    path('<int:pk>/supprimer/', views.EntryDeleteView.as_view(), name='entry_delete'),

    # Étiquettes
    path('etiquettes/', views.TagListView.as_view(), name='tag_list'),
    path('etiquettes/ajouter/', views.TagCreateView.as_view(), name='tag_create'),
    path('etiquettes/<int:pk>/', views.TagDetailView.as_view(), name='tag_detail'),
    path('etiquettes/<int:pk>/modifier/', views.TagUpdateView.as_view(), name='tag_update'),
    path('etiquettes/<int:pk>/supprimer/', views.TagDeleteView.as_view(), name='tag_delete'),
]
