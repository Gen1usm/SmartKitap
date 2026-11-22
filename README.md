# SmartKitap

## UI-компоненты

Интерфейс по референсу SmartKitap: казахский язык по умолчанию, переключение
ҚАЗ / РУС, адаптивная боковая панель, реальные данные кабинета, поиск и фильтры,
окно книги, уведомления и доступные меню действий. Технологии проекта сохранены.
Компоненты и источники графики описаны в [library/components.md](library/components.md).
Снимки браузерной проверки: `artifacts/ui-qa/` (local, ignored).

```powershell
python manage.py test --noinput
python scripts/ui_smoke.py
python scripts/browser_smoke.py
```

SmartKitap — двуязычное веб-приложение школьной электронной библиотеки на Python и Django. Проект предназначен для регистрации книг, читателей, выдачи и возврата книг, контроля сроков и подготовки простых отчётов.

## Технологии

- Python
- Django
- SQLite
- HTML5, CSS3, Bootstrap 5
- Django i18n: қазақша және русский

## Структура базы данных

- `Book` — книги: название, автор, год, жанр, описание, количество экземпляров, доступные экземпляры, обложка.
- `Reader` — читатели: ФИО, класс или группа, необязательный контакт.
- `Loan` — выдачи: книга, читатель, дата выдачи, срок возврата, дата возврата, статус.
- `StudentProfile` — связь аккаунта Django с существующим `Reader`, без копирования ФИО и контакта.
- `BookRequest` — запрос ученика, пять статусов и связь с выдачей `Loan`.
- `BookFile` — защищённый PDF/EPUB, разрешение на использование и ограничения доступа.
- `ReadingProgress` — последняя сохранённая страница PDF или глава EPUB для каждого ученика.

Просрочка не хранится как постоянный статус. Она рассчитывается автоматически: если книга активна и текущая дата позже срока возврата, выдача считается просроченной.

## Основные функции

- Публичный каталог книг с поиском, фильтрами и обложками.
- Кабинет библиотекаря с авторизацией.
- Управление книгами и читателями.
- Выдача и возврат книг.
- Автоматическое изменение количества доступных экземпляров.
- Контроль просроченных выдач.
- Статистика на главной странице.
- Отчёты: текущие выдачи, просрочки, история операций, популярные книги.
- Экспорт отчётов в CSV с кодировкой для Excel.
- Регистрация учеников, личный кабинет, собственная история запросов и выдач.
- Принятие, отклонение, резервирование и выдача по запросу.
- Онлайн-чтение PDF и EPUB с сохранением прогресса.
- Загрузка электронных книг и необязательный поиск метаданных в Open Library.

## Local setup: Windows and Linux

Use Python 3.13. From the project directory:

```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
$env:SECRET_KEY = python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
$env:DEBUG = 'true'
```

```sh
# Linux
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
export SECRET_KEY="$(python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')"
export DEBUG=true
```

Persist the generated key securely across restarts; changing it invalidates signed sessions. `.env.example` documents supported environment variables; it is not loaded automatically. Set variables in the process environment. Do not commit a real key. SQLite, public media and private media default to `db.sqlite3`, `media/`, and `private_media/`; set `SQLITE_PATH`, `MEDIA_ROOT`, and `PRIVATE_MEDIA_ROOT` to relocate them.

```sh
python manage.py migrate
python manage.py compile_locales
python manage.py createsuperuser
python manage.py runserver
```

Open http://127.0.0.1:8000/. Local HTTP requires `HTTPS_ENABLED=false` (default). For a future HTTPS deployment set `DEBUG=false`, `HTTPS_ENABLED=true`, `ALLOWED_HOSTS` and, if needed, `CSRF_TRUSTED_ORIGINS` as comma-separated values including origins' schemes. HTTPS must be configured before HSTS; enable it only when all covered subdomains support HTTPS. No forwarding headers are trusted by default. A reverse proxy may require additional configuration, only after it strips untrusted incoming headers and sets its own. `runserver` is not a production server.

```sh
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --noinput
python manage.py collectstatic --noinput
python -m playwright install chromium
python scripts/browser_smoke.py
python scripts/ui_smoke.py
```

Browser checks use temporary databases and media; screenshots and results go into ignored `artifacts/`. `collectstatic` writes to ignored `staticfiles/`. Run `python manage.py check --deploy` with the intended production settings. A clean check alone does not certify deployment security. Serve static files with the future web server. Never publicly serve `private_media/` or `media/books/`: existing PDFs use an authenticated Django view, and private books use `PrivateBookStorage`.

CSV exports use UTF-8 BOM and CSV quoting. Text containing control characters, or starting (after whitespace) with `=`, `+`, `-`, `@`, is prefixed with an apostrophe for spreadsheet import. Import fields as text; spreadsheet behavior varies, and later saves or conversions may remove this protection. The source database is unchanged.

## Как работает выдача и возврат

При обычной выдаче система проверяет, что у книги есть свободные экземпляры. Если экземпляр доступен, создаётся запись выдачи, а `available_copies` уменьшается на 1. Операция выполняется в транзакции.

При принятии запроса один экземпляр резервируется: `available_copies` уменьшается на 1, статус становится «Принята». Кнопка «Выдать книгу» создаёт обычную запись `Loan` и переводит запрос в «Выдана», без повторного уменьшения остатка. Принятый запрос можно выдать и через прежний раздел «Выдача и возврат». Отклонение принятого запроса освобождает резерв. Изменение общего количества книг учитывает активные выдачи и резервы; нельзя уменьшить фонд ниже их суммы.

При возврате система проверяет, что выдача ещё активна, ставит дату возврата, меняет статус на возвращённый и увеличивает `available_copies` на 1. Повторный возврат одной записи запрещён.

Связанный запрос при возврате получает статус «Возвращена». Запросы и выдачи защищены от удаления вместе с историей. В Django admin выдачи и запросы доступны для просмотра; операции выполняются через интерфейс библиотеки, чтобы количество книг всегда обновлялось через общий сервис.

## Оқушы және кітапханашы үшін

Оқушы `/register/` бетінде тіркеліп, `/login/` арқылы кіреді. Жеке кабинетте тек өзінің байланыс деректерін, қазіргі және бұрынғы кітаптарын көреді. Каталогтағы «Кітапты сұрату» батырмасы сұранысты кітапханашыға жібереді. Сұраныстың күйлері: **Күтуде → Қабылданды → Берілді → Қайтарылды** немесе **Қабылданбады**. Бір кітапқа бірнеше белсенді сұраныс жіберуге болмайды.

Кітапханашы `/students/` бөлімінде оқушыларды қосып, аты-жөнін, сыныбын және байланысын өңдейді, аккаунтты өшіре алады. Қолданыстағы оқырманға аккаунт қосу үшін «Қолданыстағы оқырманмен байланыстыру» өрісін таңдаңыз: бұрынғы берілімдер сақталады. `/requests/` бөлімінде сұраныстар қабылданады, қабылданбайды және кітап беріледі; қайтару бұрынғы `/loans/` бөлімінде рәсімделеді.

## Электронные книги и доступ

На странице добавления/редактирования книги можно загрузить PDF или EPUB. Для существующей книги есть отдельная кнопка «Загрузить файл книги». Максимальный размер — **25 МБ**, обложки — **2 МБ**. Проверяются расширение и содержимое: поддельные, повреждённые, зашифрованные PDF, PDF с активными действиями, некорректные EPUB и опасные пути внутри архива отклоняются. Имена электронных файлов заменяются UUID.

Загружайте только законно полученные файлы: общественное достояние, открытая лицензия или разрешение правообладателя. Загрузка требует подтверждения права использования; источник/лицензию можно указать отдельным полем. Программа не скачивает полные тексты из результатов поиска.

Электронные книги хранятся в `private_media/books/`, отдельно от публичных обложек `media/book_covers/`. Не настраивайте публичную раздачу `private_media` в веб-сервере. Django отдаёт файлы через `/books/<id>/content/` после проверки входа и доступа. Ученикам доступна включённая для онлайн-чтения книга; список «Разрешённые ученики» ограничивает её конкретными аккаунтами. Пустой список означает доступ всем вошедшим ученикам. Библиотекарь может предварительно просматривать отключённую книгу. Физическая выдача для онлайн-чтения не требуется.

PDF открывается во встроенном просмотрщике браузера внутри сайта. Прогресс PDF сохраняется **вручную**: введите последнюю прочитанную страницу и нажмите «Сохранить прогресс». Следующее открытие передаст эту страницу просмотрщику. EPUB читается по главам внутри сайта, текущую главу можно сохранить той же кнопкой. Скрипты, внешние ссылки, встроенные стили и активные элементы EPUB удаляются; безопасные иллюстрации сохраняются, сложная вёрстка и шрифты издателя могут отличаться. Замена файла сбрасывает прежний прогресс.

Интерфейс и новые статусы переведены на қазақша и русский. Переключатель языка сохраняет текущий адрес. Для компиляции изменённых `.po` каталогов на Windows выполните `python manage.py compile_locales` и перезапустите сервер; внешняя программа `gettext` для этой команды не нужна.

## Подключение PDF, уже сохранённых в media/books

Шесть предоставленных PDF подключены к существующим книгам через прежнее поле `BookFile.file`. Никакие PDF не созданы, не скачаны, не скопированы, не перемещены и не удалены. Ссылки `media/books/<реальное имя>.pdf` разрешаются хранилищем непосредственно в исходные файлы. Изменения схемы базы данных не нужны, новой миграции нет.

| Книга | Исходный файл | Страниц |
|---|---|---:|
| Абай жолы | `media/books/abay_zholy.pdf` | 280 |
| Ай мен Айша | `media/books/ai_men_aisha.pdf` | 578 |
| Ботагөз | `media/books/botagoz.pdf` | 690 |
| Көшпенділер | `media/books/koshpendiler.pdf` | 340 |
| Менің атым Қожа | `media/books/menin_atym_qoja.pdf` | 24 |
| Ұлпан | `media/books/ulpan.pdf` | 289 |

Сначала проверить соответствия, затем применить их:

```powershell
python manage.py link_book_pdfs --dry-run
python manage.py link_book_pdfs
```

Команда использует явную таблицу `library/data/pdf_book_mapping.json`. Для других книг допускается точное совпадение названия с именем файла после нормализации регистра, пробелов и дефисов; неоднозначные совпадения пропускаются. Укажите другое имя/подкаталог в JSON, если названия отличаются, или передайте свою таблицу через `--mapping путь\к\mapping.json`. Отчёт перечисляет найденные, связанные, уже связанные и отсутствующие PDF. Повторный запуск не создаёт новые книги и не заменяет отличающиеся электронные версии или настройки доступа. Если ранее загруженная копия побайтово совпадает с исходным PDF (SHA-256), ссылка переключается на файл в `media/books`; сама копия, прогресс и ограничения доступа сохраняются.

Файлы проверяются перед связыванием и чтением. В каталоге кнопка «Онлайн оқу» появляется только при включённой электронной версии и существующем файле. Если файла нет, показывается «Онлайн нұсқасы жоқ». Страница `/books/<id>/read/` содержит название, автора, встроенный просмотрщик, ручное сохранение страницы, кнопку «Каталогқа оралу» и полноэкранный режим в поддерживающих браузерах. Перелистывание и масштабирование выполняет PDF-просмотрщик браузера. Есть поддержка HTTP Range, включая большие исходные PDF «Ботагөз» и «Ұлпан»; ограничение новых загрузок в 25 МБ не мешает чтению уже сохранённых файлов.

Пути `/books/<id>/content/` и `/media/books/<реальное имя>.pdf` проверяют вход и те же ограничения доступа. Неизвестные пути не открывают произвольные файлы. Обложки продолжают раздаваться обычным способом. При развёртывании направляйте `/media/books/` в Django, чтобы веб-сервер сохранял эти проверки. Замена электронной версии в интерфейсе сохраняет исходный PDF из `media/books`.

При запуске на другом компьютере:

```powershell
python manage.py migrate
python manage.py compile_locales
python manage.py link_book_pdfs --dry-run
python manage.py link_book_pdfs
python manage.py runserver
```

Проверки существующих функций и новых ссылок не генерируют PDF; они читают предоставленный `media/books/abay_zholy.pdf`:

```powershell
python manage.py test library.tests library.test_existing_pdfs --noinput
```

Изменены: `library/storage.py`, `library/models.py` (проверка наличия, без изменения полей), `library/validators.py`, `library/views.py`, `smartkitap/urls.py`, `library/templates/library/book_actions.html`, `library/templates/library/book_read.html`, `library/static/library/css/styles.css`, `scripts/browser_smoke.py`, каталоги переводов `locale/kk/LC_MESSAGES/django.*`, `locale/ru/LC_MESSAGES/django.*` и этот README. Добавлены `library/media.py`, `library/pdf_responses.py`, `library/data/pdf_book_mapping.json`, `library/management/commands/link_book_pdfs.py`, `library/static/library/js/pdf_reader.js`, `library/test_existing_pdfs.py`, `scripts/verify_existing_pdfs.py`.


При установленном Playwright проверку реального сайта можно повторить командой ниже; пароль вводится в запросе терминала и в отчёты не сохраняется:

```powershell
python scripts/verify_existing_pdfs.py --username abai.korkem
```

## Backups

Keep the SQLite database, `media/book_covers/`, `media/books/`, and the whole `private_media/` directory together in a protected location outside Git. To get a mutually consistent snapshot, stop application writes (including file uploads) before copying the database and directories. Do not treat a normal copy of an actively written SQLite database as a guaranteed consistent backup. SQLite's backup API can snapshot a live database, but file uploads must still be coordinated for consistency with the database. Test restoration into an isolated environment. The ignored `backups/` and `artifacts/` directories are local data and are not distributed with a clone.

## Локальная демонстрация и развёртывание

Команда `python manage.py runserver` подходит для локальной демонстрации на одном компьютере. Для одновременной работы нескольких компьютеров школы понадобится развёртывание на доступном сервере или хостинге с подходящей конфигурацией, настройками безопасности и резервным копированием.

## Польза для школьного библиотекаря

SmartKitap помогает видеть наличие книг, быстро находить записи, регистрировать читателей, оформлять выдачу и возврат, контролировать просроченные книги и получать простые отчёты без ручного подсчёта.

## Ограничения текущей версии

- Нет SMS и e-mail уведомлений.
- Нет QR-сканирования.
- Нет сложной аналитики и диаграмм.
- Авторизация основана на стандартных пользователях Django.
- Для промышленного использования требуется полноценное серверное развёртывание.
