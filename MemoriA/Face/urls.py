from django.urls import path
from . import views

app_name = 'face'

urlpatterns = [
    # Page « Mes proches » (lien du menu) : un album par personne
    path('', views.PersonListView.as_view(), name='person_list'),
    path('proches/ajouter/', views.PersonCreateView.as_view(), name='person_create'),
    path('proches/<int:pk>/', views.PersonDetailView.as_view(), name='person_detail'),
    path('proches/<int:pk>/modifier/', views.PersonUpdateView.as_view(), name='person_update'),
    path('proches/<int:pk>/supprimer/', views.PersonDeleteView.as_view(), name='person_delete'),

    # Photos
    path('photos/', views.PhotoListView.as_view(), name='photo_list'),
    path('photos/ajouter/', views.PhotoCreateView.as_view(), name='photo_create'),
    path('photos/<int:pk>/', views.PhotoDetailView.as_view(), name='photo_detail'),
    path('photos/<int:pk>/modifier/', views.PhotoUpdateView.as_view(), name='photo_update'),
    path('photos/<int:pk>/supprimer/', views.PhotoDeleteView.as_view(), name='photo_delete'),

    # Visages
    path('visages/', views.FaceTagListView.as_view(), name='face_list'),
    path('photos/<int:photo_pk>/visages/ajouter/', views.FaceTagCreateView.as_view(), name='face_create'),
    path('visages/<int:pk>/', views.FaceTagDetailView.as_view(), name='face_detail'),
    path('visages/<int:pk>/modifier/', views.FaceTagUpdateView.as_view(), name='face_update'),
    path('visages/<int:pk>/supprimer/', views.FaceTagDeleteView.as_view(), name='face_delete'),
]
