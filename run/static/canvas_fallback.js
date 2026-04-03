
function deterministicHash(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
        hash = ((hash << 5) - hash) + str.charCodeAt(i);
        hash = hash & hash;
    }
    return Math.abs(hash) % 256;
}

function generateHue(name) {
    const hash = deterministicHash(name);
    return (hash * 360 / 256) | 0;
}

function drawCanvasPlaceholder(el, text, hue, mode) {
    const size = mode === 'avatar' ? 80 : 32;

    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const gradient = ctx.createLinearGradient(0, 0, size, size);
    gradient.addColorStop(0, `hsl(${hue}, 70%, 40%)`);
    gradient.addColorStop(1, `hsl(${(hue + 40) % 360}, 70%, 50%)`);

    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, size, size);

    ctx.fillStyle = 'rgba(0, 0, 0, 0.3)';
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size * 0.35, 0, Math.PI * 2);
    ctx.fill();

    ctx.font = mode === 'avatar' ? 'bold 40px Arial' : 'bold 14px Arial';
    ctx.fillStyle = 'white';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, size / 2, size / 2);

    el.src = canvas.toDataURL('image/png');

    canvas.width = 0;
    canvas.height = 0;
    ctx.clearRect(0, 0, 0, 0);
}

function handleAssetMissing(el, name, type, tier) {
    if (!el || !(el instanceof HTMLImageElement)) return;

    if (type === 'avatar') {
        const hue = generateHue(name || 'Unknown');
        const initial = (name || '?').charAt(0).toUpperCase();
        drawCanvasPlaceholder(el, initial, hue, 'avatar');
    } else if (type === 'generic') {
        const text = (name || 'A').charAt(0).toUpperCase();
        const hue = generateHue(name || '');
        drawCanvasPlaceholder(el, text, hue, 'icon');
    }
}

if (typeof window !== 'undefined') {
    window.handleAssetMissing = handleAssetMissing;
}
