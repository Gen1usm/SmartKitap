from django import template
from django.templatetags.static import static
from django.urls import reverse
from django.utils.html import format_html

from library.presentation import can_read_book

register = template.Library()


@register.simple_tag
def icon(name, css=''):
    return format_html('<svg class="ui-icon {}" width="20" height="20" viewBox="0 0 24 24" '
                       'fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
                       'stroke-linejoin="round" aria-hidden="true" focusable="false">'
                       '<use href="{}#icon-{}"></use></svg>', css, static('library/images/icons.svg'), name)


@register.simple_tag
def can_read(book, user):
    return can_read_book(book, user)


@register.simple_tag(takes_context=True)
def query_replace(context, **changes):
    params = context['request'].GET.copy()
    params.update(changes)
    return params.urlencode()


@register.inclusion_tag('library/components/nav_link.html', takes_context=True)
def nav_link(context, route, label, symbol, also=''):
    current = context['request'].resolver_match.url_name
    return {'href': reverse('library:' + route), 'label': label, 'symbol': symbol,
            'active': current in [route, *also.split(',')]}
