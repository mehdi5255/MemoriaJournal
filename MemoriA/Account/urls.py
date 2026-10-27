from django.contrib.auth import views as auth_views
from django.urls import path
from . import views

app_name = 'account'

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('inscription/', views.SignupView.as_view(), name='signup'),
    path('connexion/', auth_views.LoginView.as_view(redirect_authenticated_user=True), name='login'),
    path('deconnexion/', auth_views.LogoutView.as_view(), name='logout'),
    path('profil/', views.ProfileDetailView.as_view(), name='profile'),
    path('profil/modifier/', views.ProfileUpdateView.as_view(), name='profile_update'),
    path('profil/mot-de-passe/', views.MemoriaPasswordChangeView.as_view(), name='password_change'),
    path('statistiques/', views.StatisticsView.as_view(), name='statistics'),
    path('ai/task/<str:task_id>/', views.TaskStatusView.as_view(), name='task_status'),
]
