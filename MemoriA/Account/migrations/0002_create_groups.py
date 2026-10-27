# Migration de données : crée les groupes Utilisateur et Administrateur
from django.contrib.auth.management import create_permissions
from django.db import migrations


def create_groups(apps, schema_editor):
    # Les permissions sont normalement créées après les migrations :
    # on les crée ici pour pouvoir les donner au groupe Administrateur.
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None

    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')

    Group.objects.get_or_create(name='Utilisateur')
    admin_group, _ = Group.objects.get_or_create(name='Administrateur')
    admin_group.permissions.add(
        Permission.objects.get(codename='view_activitylog', content_type__app_label='Account')
    )


def delete_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=['Utilisateur', 'Administrateur']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('Account', '0001_initial'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(create_groups, delete_groups),
    ]
