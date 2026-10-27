from django.urls import path
from . import views

app_name = 'memory'

urlpatterns = [
    path('', views.SouvenirsView.as_view(), name='souvenirs'),

    # Récits
    path('recits/', views.StoryListView.as_view(), name='story_list'),
    path('recits/ajouter/', views.StoryCreateView.as_view(), name='story_create'),
    path('recits/<int:pk>/', views.StoryDetailView.as_view(), name='story_detail'),
    path('recits/<int:pk>/modifier/', views.StoryUpdateView.as_view(), name='story_update'),
    path('recits/<int:pk>/supprimer/', views.StoryDeleteView.as_view(), name='story_delete'),

    # Images
    path('images/', views.ImageListView.as_view(), name='image_list'),
    path('recits/<int:story_pk>/images/ajouter/', views.ImageCreateView.as_view(), name='image_create'),
    path('images/<int:pk>/', views.ImageDetailView.as_view(), name='image_detail'),
    path('images/<int:pk>/modifier/', views.ImageUpdateView.as_view(), name='image_update'),
    path('images/<int:pk>/supprimer/', views.ImageDeleteView.as_view(), name='image_delete'),
    path('images/<int:pk>/principale/', views.ImageSetMainView.as_view(), name='image_set_main'),

    # Intelligence artificielle
    path('recits/generer/', views.StoryGenerateView.as_view(), name='story_generate'),
    path('recits/<int:pk>/regenerer/', views.StoryRegenerateView.as_view(), name='story_regenerate'),
    path('recits/<int:pk>/illustrer/', views.ImageGenerateView.as_view(), name='image_generate'),
    path('retrospectives/<int:pk>/analyser/', views.RetrospectiveAnalyzeView.as_view(),
         name='retrospective_analyze'),

    # Rétrospectives
    path('retrospectives/', views.RetrospectiveListView.as_view(), name='retrospective_list'),
    path('retrospectives/ajouter/', views.RetrospectiveCreateView.as_view(), name='retrospective_create'),
    path('retrospectives/<int:pk>/', views.RetrospectiveDetailView.as_view(), name='retrospective_detail'),
    path('retrospectives/<int:pk>/modifier/', views.RetrospectiveUpdateView.as_view(), name='retrospective_update'),
    path('retrospectives/<int:pk>/supprimer/', views.RetrospectiveDeleteView.as_view(), name='retrospective_delete'),
]
