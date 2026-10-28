from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('journal/', include('Journal.urls')),
    path('voice/', include('Voice.urls')),
    path('face/', include('Face.urls')),
    path('memory/', include('Memory.urls')),
    path('reminder/', include('Reminder.urls')),
    # Account en dernier : il contiendra l'accueil, la connexion et le profil (étape 3)
    path('', include('Account.urls')),
]

# Fichiers envoyés par les utilisateurs : servis par Django en développement,
# et en ligne quand SERVE_MEDIA=True (hébergement gratuit sans serveur de fichiers)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif settings.SERVE_MEDIA:
    from django.urls import re_path
    from django.views.static import serve
    urlpatterns += [re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT})]
