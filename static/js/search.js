document.addEventListener('DOMContentLoaded', () => {
    const input = document.getElementById('search-input');
    const box = document.getElementById('search-results');
    let fuse;

    input.addEventListener('focus', async () => {
        if (!fuse) {
            const res = await fetch('/index.json');
            const data = await res.json();
            fuse = new Fuse(data, { keys: ['title', 'summary', 'category'], threshold: 0.4 });
        }
    });

    input.addEventListener('input', (e) => {
        if (!fuse) return;
        const results = fuse.search(e.target.value).slice(0, 5);
        if (results.length > 0) {
            box.innerHTML = results.map(r => `
                <a href="${r.item.permalink}" class="block p-3 hover:bg-gray-50 border-b border-gray-50 last:border-0">
                    <div class="text-xs text-blue-600 font-bold uppercase mb-1">${r.item.category}</div>
                    <div class="font-bold text-sm text-gray-900">${r.item.title}</div>
                </a>
            `).join('');
            box.classList.remove('hidden');
        } else {
            box.classList.add('hidden');
        }
    });

    document.addEventListener('click', (e) => {
        if (!input.contains(e.target) && !box.contains(e.target)) box.classList.add('hidden');
    });
});