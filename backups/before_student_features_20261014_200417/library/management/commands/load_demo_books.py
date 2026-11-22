from django.core.management.base import BaseCommand

from library.models import Book


class Command(BaseCommand):
    help = 'Загружает демонстрационные книги для каталога SmartKitap.'

    def handle(self, *args, **options):
        books = [
            {
                'title': 'Абай жолы',
                'author': 'Мұхтар Әуезов',
                'publication_year': 1942,
                'genre': 'Роман-эпопея',
                'description': 'Демонстрационная запись для проверки каталога школьной библиотеки.',
                'total_copies': 5,
                'available_copies': 5,
            },
            {
                'title': 'Менің атым Қожа',
                'author': 'Бердібек Соқпақбаев',
                'publication_year': 1957,
                'genre': 'Повесть',
                'description': 'Демонстрационная запись для проверки поиска и фильтрации.',
                'total_copies': 7,
                'available_copies': 7,
            },
            {
                'title': 'Көшпенділер',
                'author': 'Ілияс Есенберлин',
                'publication_year': 1969,
                'genre': 'Исторический роман',
                'description': 'Демонстрационная запись с казахской литературой.',
                'total_copies': 4,
                'available_copies': 4,
            },
            {
                'title': 'Ай мен Айша',
                'author': 'Шерхан Мұртаза',
                'publication_year': 1997,
                'genre': 'Роман',
                'description': 'Демонстрационная запись для учебного каталога.',
                'total_copies': 3,
                'available_copies': 3,
            },
            {
                'title': 'Ботагөз',
                'author': 'Сәбит Мұқанов',
                'publication_year': 1938,
                'genre': 'Роман',
                'description': 'Демонстрационная запись, не являющаяся реальной записью школьной библиотеки.',
                'total_copies': 6,
                'available_copies': 6,
            },
        ]

        created = 0
        updated = 0
        for book_data in books:
            _, was_created = Book.objects.update_or_create(
                title=book_data['title'],
                author=book_data['author'],
                defaults=book_data,
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Демонстрационные книги загружены. Создано: {created}, обновлено: {updated}.'
        ))
