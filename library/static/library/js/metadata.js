'use strict';
const panel = document.getElementById('metadata-lookup');
if (panel) {
    const search = document.getElementById('metadata-search');
    const status = document.getElementById('metadata-status');
    const results = document.getElementById('metadata-results');
    const importCover = document.getElementById('import-cover');
    let selectedCover = '';
    importCover.addEventListener('change', () => {
        document.getElementById('id_cover_import_id').value = importCover.checked ? selectedCover : '';
    });
    search.addEventListener('submit', async (event) => {
        event.preventDefault();
        const button = search.querySelector('button');
        button.disabled = true;
        results.replaceChildren();
        status.textContent = '…';
        try {
            const url = new URL(panel.dataset.url, window.location.origin);
            url.searchParams.set('q', document.getElementById('metadata-query').value.trim());
            const response = await fetch(url, {headers: {'Accept': 'application/json'}});
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || panel.dataset.unavailable);
            status.textContent = data.results.length ? '' : panel.dataset.empty;
            for (const item of data.results) {
                const card = document.createElement('article');
                card.className = 'metadata-card';
                if (item.cover_url) {
                    const image = document.createElement('img');
                    image.src = item.cover_url;
                    image.alt = item.title;
                    image.loading = 'lazy';
                    image.referrerPolicy = 'no-referrer';
                    card.append(image);
                }
                const title = document.createElement('strong');
                title.textContent = item.title;
                const author = document.createElement('p');
                author.textContent = item.author;
                const select = document.createElement('button');
                select.type = 'button';
                select.className = 'btn btn-sm btn-outline-primary';
                select.textContent = panel.dataset.select;
                select.addEventListener('click', async () => {
                    select.disabled = true;
                    for (const name of ['title', 'author', 'genre']) {
                        if (item[name]) document.getElementById('id_' + name).value = item[name];
                    }
                    if (item.publication_year >= 1000 && item.publication_year <= 2100) {
                        document.getElementById('id_publication_year').value = item.publication_year;
                    }
                    selectedCover = item.cover_id || '';
                    document.getElementById('id_cover_import_id').value = importCover.checked ? selectedCover : '';
                    try {
                        const workUrl = new URL(panel.dataset.url, window.location.origin);
                        workUrl.searchParams.set('work', item.id);
                        const details = await fetch(workUrl);
                        const work = await details.json();
                        if (!details.ok) throw new Error(work.error || panel.dataset.unavailable);
                        if (work.description) document.getElementById('id_description').value = work.description;
                        status.textContent = panel.dataset.selected;
                    } catch (error) {
                        status.textContent = error.message || panel.dataset.unavailable;
                    } finally {
                        select.disabled = false;
                    }
                });
                card.append(title, author, select);
                results.append(card);
            }
        } catch (error) {
            status.textContent = error.message || panel.dataset.unavailable;
        } finally {
            button.disabled = false;
        }
    });
}
