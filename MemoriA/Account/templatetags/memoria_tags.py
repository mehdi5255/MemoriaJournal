# Outils de templates partagés par toutes les apps
from django import template
from django.forms import CheckboxInput, RadioSelect, Select, SelectMultiple

register = template.Library()


@register.simple_tag
def field_widget(field):
    """Affiche le champ avec les classes Bootstrap et les attributs d'accessibilité.

    - aria-describedby relie le champ à son aide et à ses erreurs
    - aria-invalid signale un champ en erreur
    """
    widget = field.field.widget
    if isinstance(widget, CheckboxInput):
        css = 'form-check-input'
    elif isinstance(widget, (Select, SelectMultiple)) and not isinstance(widget, RadioSelect):
        css = 'form-select'
    else:
        css = 'form-control'

    described_by = []
    if field.help_text:
        described_by.append(f'{field.auto_id}_helptext')
    if field.errors:
        described_by.append(f'{field.auto_id}_errors')
        css += ' is-invalid'

    attrs = {'class': css}
    if described_by:
        attrs['aria-describedby'] = ' '.join(described_by)
    if field.errors:
        attrs['aria-invalid'] = 'true'
    return field.as_widget(attrs=attrs)
