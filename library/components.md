# SmartKitap UI

The project keeps Django templates, Bootstrap and plain JavaScript. Shared components
live in `templates/library/components/`; `templatetags/ui.py` provides the icon,
navigation and reading-permission helpers. `static/library/css/tokens.css` owns the
design tokens. `components.css` owns presentation; `components.js` owns interactions.

Components: SmartKitapLogo (`logo.html`, full/compact), Sidebar (`sidebar.html`),
TopBar (`topbar.html`), SearchInput (`search_input.html`), WelcomeBanner,
StatCard, ContinueReadingCard, BookCard, BookRecommendations, MyBooksTable,
StatusBadge, ActionMenu, FilterPanel, BookDetailsDialog, ToastNotification,
EmptyState and LoadingSkeleton. All have named template files in that directory.

Numbers come from ORM queries. Reading percentages use the real PDF page or EPUB
chapter count. Recommendations prioritize genres in the student's loan history.
Due soon means 0–3 days remaining; overdue dates are strictly before today.
Notifications reflect the current user's requests and upcoming/overdue loans;
read acknowledgements are stored in the authenticated Django session. Favorites
are omitted because the existing backend has no favorites model.

Catalog search and author/genre/availability filters use GET and work without JS.
Book details have a real URL and progressively open in a native HTML dialog. The
dialog fetch displays skeletons, a recoverable error and a regular-page fallback.
Menus close on Escape and outside clicks; the mobile drawer traps focus and
restores it. Native form validation runs before loading states. Reduced motion
is respected. Kazakh is the default language; Russian uses Django catalogs.

## Asset sources

- Local icons: [Lucide](https://lucide.dev/), downloaded SVG symbols, ISC/MIT.
  The complete required notice is `static/library/images/LUCIDE-LICENSE.txt`.
- `almaty.jpg`: [Panorame of Almaty — Ilya Rudakov](https://commons.wikimedia.org/wiki/File:Panorame_of_Almaty_-_panoramio.jpg),
  [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). Source image is
  unmodified; the UI applies CSS cropping, tint and overlays. Those visual
  adaptations are distributed under CC BY-SA 3.0. Attribution is also available
  from the application footer.
- `reading.jpg`: [Pexels, photo 13827575](https://www.pexels.com/photo/a-cup-of-coffee-on-an-open-books-near-the-potted-plant-13827575/),
  [Pexels License](https://www.pexels.com/license/). Displayed as a cropped,
  blurred decorative backdrop behind the native SVG reading corner. No third-party
  image requests are made at runtime.
- Logo, botanical artwork and reading-corner illustration: project-native SVG.
  Kazakh mottos are HTML text; decorative books have no foreign-language lettering.
- Book covers remain the existing catalog's media files.

Run `python manage.py test --noinput` for server regressions and
`python scripts/ui_smoke.py` for isolated UI/browser checks and screenshots.
