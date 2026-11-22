from pathlib import Path

import polib
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Compile project PO catalogs on Windows without external gettext binaries.'

    def handle(self, *args, **options):
        for root in settings.LOCALE_PATHS:
            for path in Path(root).glob('*/LC_MESSAGES/django.po'):
                catalog = polib.pofile(str(path))
                if catalog.fuzzy_entries() or catalog.untranslated_entries():
                    self.stderr.write(f'Incomplete translations: {path}')
                catalog.save_as_mofile(str(path.with_suffix('.mo')))
                self.stdout.write(self.style.SUCCESS(f'Compiled {path.parent.parent.name}'))
