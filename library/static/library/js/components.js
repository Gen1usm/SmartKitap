/* Progressive enhancement: every route and ordinary form works without JS. */
(() => {
    'use strict';
    const $ = (selector, root = document) => root.querySelector(selector);
    const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
    const focusable = root => $$('a[href],button:not(:disabled),input:not(:disabled),select:not(:disabled),summary,[tabindex="0"]', root).filter(el => el.getClientRects().length);

    function prepareCovers(root = document) {
        $$('[data-cover-image]', root).forEach(img => {
            const fallback = () => { img.hidden = true; $('.cover-fallback', img.parentElement).hidden = false; };
            img.addEventListener('error', fallback, {once: true});
            if (img.complete && !img.naturalWidth) fallback();
        });
    }
    prepareCovers();

    $$('[data-search-input]').forEach(root => {
        const input = $('input', root), clear = $('[data-search-clear]', root);
        const sync = () => { clear.hidden = !input.value; };
        input.addEventListener('input', sync);
        clear.addEventListener('click', () => { input.value = ''; sync(); input.focus(); input.dispatchEvent(new Event('input', {bubbles: true})); });
        sync();
    });

    const sidebar = $('#app-sidebar'), drawerButton = $('[data-drawer-open]');
    const backdrop = $('.drawer-backdrop'), content = $('.content-wrapper');
    let drawerReturnFocus;
    function closeDrawer(restore = true) {
        document.body.classList.remove('drawer-open');
        backdrop.hidden = true;
        drawerButton.setAttribute('aria-expanded', 'false');
        sidebar.removeAttribute('role'); sidebar.removeAttribute('aria-modal');
        content.inert = false;
        if (restore && drawerReturnFocus) drawerReturnFocus.focus();
    }
    drawerButton.addEventListener('click', () => {
        drawerReturnFocus = document.activeElement;
        document.body.classList.add('drawer-open');
        backdrop.hidden = false;
        drawerButton.setAttribute('aria-expanded', 'true');
        sidebar.setAttribute('role', 'dialog'); sidebar.setAttribute('aria-modal', 'true');
        content.inert = true;
        focusable(sidebar)[0]?.focus();
    });
    $$('[data-drawer-close]').forEach(el => el.addEventListener('click', () => closeDrawer()));
    matchMedia('(min-width: 761px)').addEventListener('change', event => { if (event.matches) closeDrawer(false); });

    function closePopups(except) {
        $$('[data-popup][open]').filter(item => item !== except).forEach(item => item.open = false);
    }
    function positionActionMenu(details) {
        if (!details.classList.contains('action-menu') || !details.open) return;
        const panel = $('.popup-panel', details), button = $('summary', details).getBoundingClientRect();
        const width = panel.offsetWidth, height = panel.offsetHeight;
        const left = Math.max(10, Math.min(button.right - width, innerWidth - width - 10));
        const top = button.bottom + height + 8 > innerHeight ? Math.max(10, button.top - height - 4) : button.bottom + 4;
        panel.style.setProperty('--popup-left', left + 'px'); panel.style.setProperty('--popup-top', top + 'px');
    }
    $$('[data-popup]').forEach(item => {
        item.addEventListener('toggle', () => {
            $('summary', item).setAttribute('aria-expanded', String(item.open));
            if (item.open) { closePopups(item); positionActionMenu(item); }
        });
        $('summary', item).addEventListener('keydown', event => {
            if (event.key === 'ArrowDown') { event.preventDefault(); item.open = true; requestAnimationFrame(() => $('a,button', $('.popup-panel', item))?.focus()); }
        });
    });
    document.addEventListener('click', event => { if (!event.target.closest('[data-popup]')) closePopups(); });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            const opened = $('[data-popup][open]');
            if (opened) { event.preventDefault(); opened.open = false; $('summary', opened).focus(); }
            if (document.body.classList.contains('drawer-open')) { event.preventDefault(); closeDrawer(); }
        }
        if (event.key === 'Tab' && document.body.classList.contains('drawer-open')) {
            const items = focusable(sidebar), first = items[0], last = items.at(-1);
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
    });
    document.addEventListener('scroll', () => $$('[data-popup].action-menu[open]').forEach(positionActionMenu), true);
    window.addEventListener('resize', () => $$('[data-popup].action-menu[open]').forEach(positionActionMenu));

    const dialog = $('#book-details-dialog'), dialogContent = $('[data-dialog-content]', dialog);
    let dialogURL, dialogTrigger, controller, fetchId = 0;
    async function loadBook(url) {
        const currentId = ++fetchId;
        controller?.abort();
        const requestController = new AbortController(); controller = requestController;
        dialogURL = url;
        dialogContent.replaceChildren($('#book-loading-template').content.cloneNode(true));
        dialogContent.setAttribute('aria-busy', 'true');
        const timer = setTimeout(() => requestController.abort(), 15000);
        try {
            const response = await fetch(url, {headers: {'X-Requested-With': 'SmartKitap'}, signal: requestController.signal});
            if (!response.ok || response.redirected) throw new Error('Book unavailable');
            const html = await response.text();
            if (currentId !== fetchId || !dialog.open) return;
            dialogContent.innerHTML = html;
            prepareCovers(dialogContent);
        } catch (error) {
            if (currentId !== fetchId || !dialog.open) return;
            dialogContent.replaceChildren($('#book-error-template').content.cloneNode(true));
            $('[data-dialog-fallback]', dialogContent).href = url;
        } finally {
            clearTimeout(timer);
            if (currentId === fetchId) dialogContent.removeAttribute('aria-busy');
        }
    }
    document.addEventListener('click', event => {
        const link = event.target.closest('a[data-book-dialog]');
        if (link && !event.ctrlKey && !event.metaKey && !event.shiftKey && event.button === 0 && typeof dialog.showModal === 'function') {
            event.preventDefault(); closePopups(); dialogTrigger = link;
            if (!dialog.open) dialog.showModal();
            loadBook(link.href);
        }
        if (event.target.closest('[data-dialog-retry]')) loadBook(dialogURL);
    });
    $('[data-dialog-close]', dialog).addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', event => {
        const rect = dialog.getBoundingClientRect();
        if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
    });
    dialog.addEventListener('close', () => { ++fetchId; controller?.abort(); dialogTrigger?.focus(); });

    function toast(text, tone = 'info') {
        const node = document.createElement('div'); node.className = 'toast-notification toast-' + tone;
        node.setAttribute('role', tone === 'error' ? 'alert' : 'status'); node.setAttribute('data-toast', '');
        const message = document.createElement('span'); message.textContent = text; node.append(message);
        const button = document.createElement('button'); button.type = 'button'; button.className = 'icon-button';
        button.setAttribute('data-toast-close', ''); button.setAttribute('aria-label', $('[data-dialog-close]').getAttribute('aria-label'));
        button.append($('[data-dialog-close] svg').cloneNode(true)); node.append(button); $('.toast-region').append(node);
    }
    document.addEventListener('click', event => { event.target.closest('[data-toast-close]')?.closest('[data-toast]')?.remove(); });
    const notificationForm = $('[data-notifications-read]');
    notificationForm?.addEventListener('submit', async event => {
        event.preventDefault();
        const button = $('button', notificationForm); button.disabled = true; button.classList.add('is-loading');
        try {
            const response = await fetch(notificationForm.action, {method: 'POST', body: new FormData(notificationForm), headers: {'X-Requested-With': 'SmartKitap'}});
            if (!response.ok) throw new Error('Acknowledgement failed');
            await response.json();
            $('.notification-dot')?.remove(); $('.notification-count').textContent = '0';
            $$('.notification-item.unread').forEach(el => el.classList.remove('unread'));
            notificationForm.hidden = true; toast(document.body.dataset.notificationSuccess, 'success');
        } catch (error) { toast(document.body.dataset.networkError, 'error'); }
        finally { button.disabled = false; button.classList.remove('is-loading'); }
    });

    document.addEventListener('submit', event => {
        const form = event.target;
        if (form.hasAttribute('data-notifications-read') || event.defaultPrevented) return;
        if (form.dataset.submitting) { event.preventDefault(); return; }
        form.dataset.submitting = 'true'; form.setAttribute('aria-busy', 'true');
        const button = event.submitter;
        if (button) { button.classList.add('is-loading'); button.setAttribute('aria-disabled', 'true'); }
        // Keep submitter names/values (language, approve/reject) in the native request.
    });
    window.addEventListener('pageshow', () => {
        $$('form[data-submitting]').forEach(form => { delete form.dataset.submitting; form.removeAttribute('aria-busy'); });
        $$('.is-loading').forEach(button => { button.classList.remove('is-loading'); button.removeAttribute('aria-disabled'); });
    });
})();
