// ═══════════════════════════════════════════════════════════════
// Crypto News Engine Dashboard — main.js (Classroom Theme)
// Vanilla JS SPA — No frameworks, no build tools
// ═══════════════════════════════════════════════════════════════

// ─── State ─────────────────────────────────────────────────────
let currentTab = 'overview';
let refreshInterval = null;
const REFRESH_MS = 30000;

// Chart instances (destroy before re-render)
let categoryChart = null;
let sentimentChart = null;
let priceChart = null;
let categoryPieChart = null;

// Chart control state
let priceAsset = 'BTC';

// ─── Init ──────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', init);

function init() {
    setupTabs();
    setupModal();
    loadTab('overview');
    startAutoRefresh();
    updateClock();
    setInterval(updateClock, 1000);
    fetchHeaderStatus();
    setInterval(fetchHeaderStatus, 15000);
}

// ─── Tab System ────────────────────────────────────────────────
function setupTabs() {
    document.querySelectorAll('.dtab').forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            if (tab === currentTab) return;
            document.querySelectorAll('.dtab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            loadTab(tab);
        });
    });
}

function loadTab(name) {
    currentTab = name;
    destroyAllCharts();
    const content = document.getElementById('content');
    content.innerHTML = '<div class="loading-msg">Loading...</div>';

    switch (name) {
        case 'overview':  loadOverview(); break;
        case 'news':      loadNews(); break;
        case 'signals':   loadSignals(); break;
        case 'analysis':  loadAnalysis(); break;
        case 'predictions': loadPredictions(); break;
        case 'projects':  loadProjects(); break;
        default:          content.innerHTML = '<div class="error-msg">Unknown tab</div>';
    }
}

// ─── Modal ─────────────────────────────────────────────────────
function setupModal() {
    const overlay = document.getElementById('modal-overlay');
    const closeBtn = document.getElementById('modal-close');

    closeBtn.addEventListener('click', closeModal);
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) closeModal();
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeModal();
    });
}

function openModal(html) {
    document.getElementById('modal-body').innerHTML = html;
    document.getElementById('modal-overlay').classList.remove('hidden');
}

function closeModal() {
    document.getElementById('modal-overlay').classList.add('hidden');
    document.getElementById('modal-body').innerHTML = '';
}

// ═══════════════════════════════════════════════════════════════
//  OVERVIEW TAB — Market Pulse
// ═══════════════════════════════════════════════════════════════

async function loadOverview() {
    const [movers, feed, signals, pulse, stats] = await Promise.all([
        fetchJSON('/api/movers'),
        fetchJSON('/api/impact-feed?limit=15'),
        fetchJSON('/api/signals'),
        fetchJSON('/api/hourly-pulse?asset=BTC&hours=24'),
        fetchJSON('/api/stats'),
    ]);

    let html = '';

    // ── TOP MOVERS ──────────────────────────────────────────────
    html += '<h3 class="section-title">Market Pulse</h3>';
    html += '<div class="movers-grid">';
    if (movers && movers.length > 0) {
        movers.forEach(m => {
            const chg1h = m.chg_1h_pct || 0;
            const chg24h = m.chg_24h_pct || 0;
            const chgClass1h = chg1h > 0.3 ? 'profit' : chg1h < -0.3 ? 'loss' : 'flat-text';
            const chgClass24h = chg24h > 0.3 ? 'profit' : chg24h < -0.3 ? 'loss' : 'flat-text';
            const arrow1h = chg1h > 0.3 ? '▲' : chg1h < -0.3 ? '▼' : '→';
            const arrow24h = chg24h > 0.3 ? '▲' : chg24h < -0.3 ? '▼' : '→';
            const sentBadge = m.sentiment === 'bullish' ? 'sent-bull' : m.sentiment === 'bearish' ? 'sent-bear' : m.sentiment === 'quiet' ? 'sent-quiet' : 'sent-mixed';

            // Significance bar width (0-100%)
            const sigPct = Math.min(100, (m.significance / 10) * 100);
            const sigColor = m.significance >= 5 ? '#ee8888' : m.significance >= 2 ? '#f0e090' : '#a8c8a0';

            html += `
            <div class="mover-card">
                <div class="mover-header">
                    <span class="mover-asset">${escapeHtml(m.asset)}</span>
                    <span class="mover-price">$${formatNum(m.price)}</span>
                    <span class="badge ${sentBadge}">${m.sentiment.toUpperCase()}</span>
                </div>
                <div class="mover-changes">
                    <span class="${chgClass1h}"><b>1h:</b> ${arrow1h} ${chg1h >= 0 ? '+' : ''}${chg1h.toFixed(2)}%</span>
                    <span class="${chgClass24h}"><b>24h:</b> ${arrow24h} ${chg24h >= 0 ? '+' : ''}${chg24h.toFixed(2)}%</span>
                </div>
                <div class="mover-news">
                    ${m.news_count > 0
                        ? `<span class="news-count">${m.news_count} news hit${m.news_count > 1 ? 's' : ''}</span>
                           <span class="cat-badge cat-${(m.top_category || '').toLowerCase()}">${m.top_category || ''}</span>
                           ${m.max_severity >= 3 ? `<span class="sev-badge sev-${m.max_severity >= 4 ? 'high' : 'med'}">SEV ${m.max_severity}</span>` : ''}`
                        : '<span class="no-news">No recent news</span>'}
                </div>
                ${m.top_headline ? `<div class="mover-headline">${escapeHtml(m.top_headline)}</div>` : ''}
                <div class="sig-bar-wrap">
                    <div class="sig-bar" style="width:${sigPct}%;background:${sigColor}"></div>
                </div>
            </div>`;
        });
    } else {
        html += '<div class="empty-state">No price data available</div>';
    }
    html += '</div>';

    // ── HOURLY TIMELINE (BTC) ───────────────────────────────────
    html += '<div class="sub-section">';
    html += '<h3 class="section-title">Hourly Timeline — BTC (Last 24h)</h3>';
    html += '<div class="hourly-timeline" id="hourly-timeline">';
    if (pulse && pulse.length > 0) {
        pulse.forEach(h => {
            const chg = h.change_pct || 0;
            const barH = Math.min(40, Math.abs(chg) * 15);
            const barColor = chg > 0 ? 'var(--green)' : chg < 0 ? 'var(--red)' : 'var(--chalk-dim)';
            const time = h.timestamp ? h.timestamp.substring(11, 16) : '';
            const newsMarker = h.news_count > 0
                ? `<div class="hour-news">${'★'.repeat(Math.min(3, h.news_count))}</div>`
                : '';

            html += `
            <div class="hour-bar" title="${time}: ${chg >= 0 ? '+' : ''}${chg.toFixed(2)}% | ${h.news_count} news">
                <div class="hour-bar-fill" style="height:${barH}px;background:${barColor}"></div>
                ${newsMarker}
                <div class="hour-label">${time}</div>
            </div>`;
        });
    } else {
        html += '<div class="empty-state">No hourly data</div>';
    }
    html += '</div></div>';

    // ── IMPACT FEED ─────────────────────────────────────────────
    html += '<div class="sub-section">';
    html += '<h3 class="section-title">Significant Events</h3>';
    if (feed && feed.length > 0) {
        feed.forEach(f => {
            const levelClass = f.level === 'CRITICAL' ? 'impact-critical' : f.level === 'NOTABLE' ? 'impact-notable' : 'impact-minor';
            const levelIcon = f.level === 'CRITICAL' ? '🔥' : f.level === 'NOTABLE' ? '⚠️' : 'ℹ️';
            const dirClass = f.direction === 'BULLISH' ? 'dir-long' : f.direction === 'BEARISH' ? 'dir-short' : 'dir-neutral';

            let impactHtml = '';
            if (f.asset_impacts && f.asset_impacts.length > 0) {
                impactHtml = f.asset_impacts.map(ai => {
                    if (ai.change_pct !== null) {
                        const cls = ai.change_pct > 0 ? 'profit' : ai.change_pct < 0 ? 'loss' : 'flat-text';
                        return `<span class="impact-asset"><b>${ai.asset}</b> <span class="${cls}">${ai.change_pct >= 0 ? '+' : ''}${ai.change_pct.toFixed(2)}%</span></span>`;
                    }
                    return `<span class="impact-asset"><b>${ai.asset}</b> <span class="flat-text">pending</span></span>`;
                }).join(' ');
            }

            html += `
            <div class="impact-card ${levelClass}">
                <div class="impact-header">
                    <span class="impact-level">${levelIcon} ${f.level}</span>
                    <span class="impact-time">${timeAgo(f.detected_at)}</span>
                    <span class="${dirClass}">${f.direction}</span>
                </div>
                <div class="impact-headline">${escapeHtml(f.headline)}</div>
                <div class="impact-meta">
                    <span class="cat-badge cat-${(f.category || '').toLowerCase()}">${f.category}</span>
                    <span class="sev-badge sev-${f.severity >= 4 ? 'high' : f.severity >= 3 ? 'med' : 'low'}">SEV ${f.severity}</span>
                    <span>${f.article_count} source${f.article_count > 1 ? 's' : ''}</span>
                </div>
                ${impactHtml ? `<div class="impact-assets">${impactHtml}</div>` : ''}
            </div>`;
        });
    } else {
        html += '<div class="empty-state">No significant events detected</div>';
    }
    html += '</div>';

    // ── ACTIVE SIGNALS (compact) ────────────────────────────────
    if (signals && signals.length > 0) {
        html += '<div class="sub-section">';
        html += '<h3 class="section-title">Active Signals</h3>';
        html += '<div class="signal-grid">';
        signals.forEach(s => { html += renderSignalCard(s); });
        html += '</div></div>';
    }

    document.getElementById('content').innerHTML = html;
    updateLastRefresh();
}

function renderStatCard(value, label, isSmall) {
    const cls = isSmall ? 'stat-value" style="font-size:0.9rem' : 'stat-value';
    return `
        <div class="stat-card">
            <div class="${cls}">${value}</div>
            <div class="stat-label">${label}</div>
        </div>
    `;
}

function renderSignalCard(s) {
    const dirClass = s.direction === 'long' ? 'dir-long' : 'dir-short';
    const confClass = s.confidence === 'HIGH' ? 'confidence-high' : 'confidence-medium';
    const score = parseFloat(s.signal_score || 0);
    const news = parseFloat(s.news_component || 0);
    const market = parseFloat(s.market_component || 0);
    const narrative = parseFloat(s.narrative_component || 0);
    const novelty = parseFloat(s.novelty_component || 0);
    const maxFactor = 1.0; // normalize to 1.0

    return `
        <div class="signal-card">
            <div class="signal-card-header">
                <span class="signal-asset">${escapeHtml(s.asset)}</span>
                <span class="${dirClass}">${s.direction.toUpperCase()}</span>
                <span class="${confClass}">${s.confidence}</span>
                <span class="signal-score">${score.toFixed(2)}</span>
            </div>
            <div class="signal-factors">
                ${renderFactorBar('News', news, maxFactor, 'news')}
                ${renderFactorBar('Market', market, maxFactor, 'market')}
                ${renderFactorBar('Narr.', narrative, maxFactor, 'narrative')}
                ${renderFactorBar('Novel.', novelty, maxFactor, 'novelty')}
            </div>
            ${s.reasoning ? `<div class="signal-reasoning">${escapeHtml(s.reasoning)}</div>` : ''}
        </div>
    `;
}

function renderFactorBar(label, value, max, cls) {
    const pct = Math.min(100, (value / max) * 100);
    return `
        <div class="factor-bar">
            <div class="factor-bar-label">${label}</div>
            <div class="factor-bar-track">
                <div class="factor-bar-fill ${cls}" style="width:${pct}%"></div>
            </div>
            <div class="factor-bar-value">${value.toFixed(2)}</div>
        </div>
    `;
}

function renderNarrativeCard(n) {
    const keywords = Array.isArray(n.keywords) ? n.keywords : [];
    const impact = parseFloat(n.avg_price_impact || 0);
    const impactClass = impact >= 0 ? 'profit' : 'loss';

    return `
        <div class="narrative-card">
            <div class="narrative-name">${escapeHtml(n.name)}</div>
            <div class="narrative-meta">
                <span>Events: ${n.event_count || 0}</span>
                <span>Last: ${timeAgo(n.last_seen)}</span>
                <span class="${impactClass}">Impact: ${impact.toFixed(2)}%</span>
            </div>
            <div class="narrative-keywords">
                ${keywords.slice(0, 6).map(k => `<span class="keyword-chip">${escapeHtml(k)}</span>`).join('')}
            </div>
        </div>
    `;
}

function renderCategoryBarChart(data) {
    const el = document.getElementById('category-chart');
    if (!el) return;

    const cats = data.map(d => d.category);
    const counts = data.map(d => d.count);
    const colors = cats.map(c => getCategoryColor(c));

    const options = {
        series: [{ name: 'Events', data: counts }],
        chart: {
            type: 'bar',
            height: 260,
            background: 'transparent',
            toolbar: { show: false },
        },
        plotOptions: {
            bar: { horizontal: true, barHeight: '65%', borderRadius: 2, distributed: true }
        },
        colors: colors,
        dataLabels: {
            enabled: true,
            style: { fontSize: '11px', fontFamily: 'Courier New, monospace', colors: ['#f5f0dc'] },
            formatter: (val) => val
        },
        xaxis: {
            categories: cats,
            labels: { style: { colors: '#f5f0dc', fontFamily: 'Courier New, monospace', fontSize: '10px' } },
            axisBorder: { color: 'rgba(248,240,220,0.15)' }
        },
        yaxis: {
            labels: { style: { colors: '#f5f0dc', fontFamily: 'DM Sans, sans-serif', fontSize: '10px' } }
        },
        grid: {
            borderColor: 'rgba(248,240,220,0.08)',
            xaxis: { lines: { show: true } },
            yaxis: { lines: { show: false } }
        },
        tooltip: { theme: 'dark' },
        legend: { show: false },
        theme: { mode: 'dark' }
    };

    categoryChart = new ApexCharts(el, options);
    categoryChart.render();
}

// ═══════════════════════════════════════════════════════════════
//  NEWS TAB
// ═══════════════════════════════════════════════════════════════

async function loadNews() {
    const [articles, clusters, sources] = await Promise.all([
        fetchJSON('/api/articles?limit=50'),
        fetchJSON('/api/clusters?limit=50'),
        fetchJSON('/api/sources'),
    ]);

    let html = '';

    // --- Articles section ---
    html += '<div class="sub-section">';
    html += '<div class="flex-between mb-1">';
    html += '<h2 class="section-title" style="border:none;margin:0;padding:0">Recent Articles</h2>';
    html += '<select id="source-filter" class="filter-select"><option value="">All Sources</option>';
    if (sources) {
        // Group: show major sources first, then GDELT
        const major = sources.filter(s => !s.startsWith('GDELT/'));
        const gdelt = sources.filter(s => s.startsWith('GDELT/'));
        major.forEach(s => { html += `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`; });
        if (gdelt.length > 0) {
            html += '<option disabled>--- GDELT Sources ---</option>';
            gdelt.slice(0, 30).forEach(s => { html += `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`; });
            if (gdelt.length > 30) html += `<option disabled>... +${gdelt.length - 30} more</option>`;
        }
    }
    html += '</select>';
    html += '</div>';

    if (!articles || articles.length === 0) {
        html += '<div class="empty-state">No articles found</div>';
    } else {
        html += `
            <div class="table-container" style="max-height:400px;overflow-y:auto">
                <table class="trade-table">
                    <thead>
                        <tr>
                            <th>Published</th>
                            <th>Source</th>
                            <th>Title</th>
                        </tr>
                    </thead>
                    <tbody id="articles-tbody">
                        ${articles.map(a => renderArticleRow(a)).join('')}
                    </tbody>
                </table>
            </div>
        `;
    }
    html += '</div>';

    // --- Clusters section ---
    html += '<div class="sub-section">';
    html += '<h2 class="section-title">Event Clusters</h2>';
    if (!clusters || clusters.length === 0) {
        html += '<div class="empty-state">No clusters found</div>';
    } else {
        html += `
            <div class="table-container" style="max-height:450px;overflow-y:auto">
                <table class="trade-table">
                    <thead>
                        <tr>
                            <th>Headline</th>
                            <th>Category</th>
                            <th>Sev</th>
                            <th>Sentiment</th>
                            <th>Articles</th>
                            <th>Novelty</th>
                            <th>Assets</th>
                            <th>Detected</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${clusters.map(c => renderClusterRow(c)).join('')}
                    </tbody>
                </table>
            </div>
        `;
    }
    html += '</div>';

    document.getElementById('content').innerHTML = html;

    // Source filter handler
    const filterEl = document.getElementById('source-filter');
    if (filterEl) {
        filterEl.addEventListener('change', async () => {
            const source = filterEl.value;
            const url = source ? `/api/articles?limit=50&source=${encodeURIComponent(source)}` : '/api/articles?limit=50';
            const filtered = await fetchJSON(url);
            const tbody = document.getElementById('articles-tbody');
            if (tbody && filtered) {
                tbody.innerHTML = filtered.map(a => renderArticleRow(a)).join('');
            }
        });
    }

    updateLastRefresh();
}

function renderArticleRow(a) {
    const sourceClass = getSourceClass(a.source);
    return `
        <tr>
            <td>${formatTime(a.published_at)}</td>
            <td><span class="source-badge ${sourceClass}">${escapeHtml(truncate(a.source, 20))}</span></td>
            <td class="wrap-cell"><a href="${escapeHtml(a.url)}" target="_blank" rel="noopener" class="article-link">${escapeHtml(a.title)}</a></td>
        </tr>
    `;
}

function renderClusterRow(c) {
    const catClass = 'cat-' + (c.category || 'UNKNOWN');
    const sevClass = getSeverityClass(c.severity);
    const sent = parseFloat(c.sentiment || 0);
    const sentClass = sent > 0 ? 'positive' : (sent < 0 ? 'negative' : 'neutral');
    const novelty = parseFloat(c.novelty_score || 0);
    const assets = Array.isArray(c.assets_affected) ? c.assets_affected : [];

    return `
        <tr>
            <td class="wrap-cell" style="min-width:200px">${escapeHtml(c.representative_headline || '--')}</td>
            <td><span class="cat-badge ${catClass}">${escapeHtml(c.category)}</span></td>
            <td><span class="sev-badge ${sevClass}">${c.severity}</span></td>
            <td>
                <span class="sentiment-value ${sentClass}">${sent.toFixed(2)}</span>
            </td>
            <td style="text-align:center">${c.article_count}</td>
            <td>
                <div class="novelty-bar"><div class="novelty-fill" style="width:${(novelty * 100).toFixed(0)}%"></div></div>
                <span style="font-size:0.65rem;margin-left:4px">${novelty.toFixed(2)}</span>
            </td>
            <td>${assets.map(a => `<span class="keyword-chip">${escapeHtml(a)}</span>`).join(' ')}</td>
            <td>${formatTime(c.first_detected_at)}</td>
        </tr>
    `;
}

// ═══════════════════════════════════════════════════════════════
//  SIGNALS TAB
// ═══════════════════════════════════════════════════════════════

async function loadSignals() {
    const signals = await fetchJSON('/api/signals');

    let html = '<h2 class="section-title">Multi-Factor Signals (v2)</h2>';

    if (!signals || signals.length === 0) {
        html += '<div class="empty-state">No active signals -- pipeline generates these during daily runs</div>';
    } else {
        html += `
            <div class="table-container">
                <table class="trade-table">
                    <thead>
                        <tr>
                            <th>Asset</th>
                            <th>Direction</th>
                            <th>Confidence</th>
                            <th>Score</th>
                            <th>Factors (N/M/Na/No)</th>
                            <th>Entry Time</th>
                            <th>Price</th>
                            <th>Momentum 1h</th>
                            <th>Vol Z-Score</th>
                            <th>Confirmation</th>
                            <th>Reasoning</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${signals.map(s => renderSignalRow(s)).join('')}
                    </tbody>
                </table>
            </div>
        `;
    }

    document.getElementById('content').innerHTML = html;
    updateLastRefresh();
}

function renderSignalRow(s) {
    const dirClass = s.direction === 'long' ? 'dir-long' : 'dir-short';
    const confClass = s.confidence === 'HIGH' ? 'confidence-high' : 'confidence-medium';
    const score = parseFloat(s.signal_score || 0);
    const news = parseFloat(s.news_component || 0);
    const market = parseFloat(s.market_component || 0);
    const narrative = parseFloat(s.narrative_component || 0);
    const novelty = parseFloat(s.novelty_component || 0);
    const momentum = parseFloat(s.momentum_1h || 0);
    const volZ = parseFloat(s.volume_zscore || 0);
    const factors = Array.isArray(s.confirmation_factors) ? s.confirmation_factors : [];

    return `
        <tr>
            <td style="font-weight:700">${escapeHtml(s.asset)}</td>
            <td><span class="${dirClass}">${s.direction.toUpperCase()}</span></td>
            <td><span class="${confClass}">${s.confidence}</span></td>
            <td>
                <div class="score-meter">
                    <div class="score-meter-bar"><div class="score-meter-fill" style="width:${(score * 100).toFixed(0)}%"></div></div>
                    <span class="score-meter-value">${score.toFixed(2)}</span>
                </div>
            </td>
            <td>
                <div class="mini-factors" title="News: ${news.toFixed(2)}, Market: ${market.toFixed(2)}, Narrative: ${narrative.toFixed(2)}, Novelty: ${novelty.toFixed(2)}">
                    <div class="mini-factor"><div class="mini-factor-fill news" style="height:${(news * 100).toFixed(0)}%"></div></div>
                    <div class="mini-factor"><div class="mini-factor-fill market" style="height:${(market * 100).toFixed(0)}%"></div></div>
                    <div class="mini-factor"><div class="mini-factor-fill narrative" style="height:${(narrative * 100).toFixed(0)}%"></div></div>
                    <div class="mini-factor"><div class="mini-factor-fill novelty" style="height:${(novelty * 100).toFixed(0)}%"></div></div>
                </div>
            </td>
            <td>${formatTime(s.entry_time)}</td>
            <td>${s.price_at_signal ? formatPrice(s.price_at_signal) : '--'}</td>
            <td class="${momentum >= 0 ? 'profit' : 'loss'}">${momentum.toFixed(3)}%</td>
            <td>${volZ.toFixed(2)}</td>
            <td class="wrap-cell" style="font-size:10px;max-width:150px">${factors.map(f => escapeHtml(String(f))).join(', ') || '--'}</td>
            <td class="wrap-cell" style="font-size:10px;max-width:250px;font-style:italic">${escapeHtml(s.reasoning || '--')}</td>
        </tr>
    `;
}

// ═══════════════════════════════════════════════════════════════
//  PREDICTIONS TAB
// ═══════════════════════════════════════════════════════════════

async function loadPredictions() {
    const [preds, accuracy, trust] = await Promise.all([
        fetchJSON('/api/predictions?limit=50'),
        fetchJSON('/api/predictions/accuracy'),
        fetchJSON('/api/source-trust'),
    ]);

    let html = '';

    // ── Accuracy overview ───────────────────────────────────────
    html += '<h3 class="section-title">Prediction Accuracy</h3>';
    html += '<div class="roster-summary">';
    if (accuracy) {
        for (const tf of ['1h', '4h', '24h']) {
            const s = accuracy[tf] || {};
            const acc = s.accuracy || 0;
            const accClass = acc >= 0.6 ? 'profit' : acc >= 0.45 ? 'flat-text' : 'loss';
            html += `
                <div class="stat-card">
                    <div class="stat-value ${accClass}">${(acc * 100).toFixed(1)}%</div>
                    <div class="stat-label">${tf} Accuracy (${s.correct || 0}/${s.total || 0})</div>
                    <div class="grade-bar-row">
                        ${renderGradeBar(s.by_grade || {})}
                    </div>
                </div>`;
        }
    }
    html += '</div>';

    // ── Source Trust Leaderboard ─────────────────────────────────
    html += '<h3 class="section-title">Source Trust Rating</h3>';
    if (trust && trust.length > 0) {
        html += '<div class="table-container"><table class="trade-table">';
        html += `<thead><tr>
            <th>Source</th><th>Trust Score</th><th>1h Acc</th><th>4h Acc</th><th>24h Acc</th><th>Predictions</th><th>Status</th>
        </tr></thead><tbody>`;
        trust.forEach(s => {
            const trustPct = (s.trust_score * 100).toFixed(0);
            const trustClass = s.trust_score >= 0.6 ? 'profit' : s.trust_score >= 0.4 ? 'flat-text' : 'loss';
            const barW = Math.min(100, s.trust_score * 100);
            const barColor = s.trust_score >= 0.6 ? 'var(--green)' : s.trust_score >= 0.4 ? 'var(--chalk-yellow)' : 'var(--red)';
            const noiseTag = s.is_noise_source ? '<span class="badge sent-bear">NOISE</span>' : '<span class="badge sent-bull">OK</span>';

            html += `<tr>
                <td><b>${escapeHtml(s.source_name)}</b></td>
                <td>
                    <span class="${trustClass}" style="font-weight:700">${trustPct}%</span>
                    <div class="sig-bar-wrap" style="margin-top:2px"><div class="sig-bar" style="width:${barW}%;background:${barColor}"></div></div>
                </td>
                <td>${formatAccuracy(s.accuracy_1h)}</td>
                <td>${formatAccuracy(s.accuracy_4h)}</td>
                <td>${formatAccuracy(s.accuracy_24h)}</td>
                <td>${s.total_predictions}</td>
                <td>${noiseTag}</td>
            </tr>`;
        });
        html += '</tbody></table></div>';
    } else {
        html += '<div class="empty-state">No source trust data yet — run predictions first</div>';
    }

    // ── Recent Predictions ──────────────────────────────────────
    html += '<h3 class="section-title">Recent Predictions</h3>';
    if (preds && preds.length > 0) {
        preds.forEach(p => {
            const dirClass = p.direction === 'bullish' ? 'dir-long' : 'dir-short';
            const confPct = ((p.confidence || 0) * 100).toFixed(0);
            const confClass = p.confidence >= 0.7 ? 'confidence-high' : p.confidence >= 0.45 ? 'confidence-medium' : 'confidence-low';

            // Grade badges
            const g1h = renderGradeBadge(p.grade_1h, p.change_1h_pct);
            const g4h = renderGradeBadge(p.grade_4h, p.change_4h_pct);
            const g24h = renderGradeBadge(p.grade_24h, p.change_24h_pct);

            const sources = Array.isArray(p.sources) ? p.sources : [];

            html += `
            <div class="prediction-card ${p.graded ? '' : 'pending-grade'}">
                <div class="pred-header">
                    <span class="mover-asset">${escapeHtml(p.asset)}</span>
                    <span class="${dirClass}">${(p.direction || '').toUpperCase()}</span>
                    <span class="${confClass}">${confPct}% conf</span>
                    <span class="cat-badge cat-${(p.category || '').toLowerCase()}">${p.category || ''}</span>
                    <span class="pred-time">${timeAgo(p.predicted_at)}</span>
                </div>
                <div class="pred-grades">
                    <span class="pred-grade-item">1h: ${g1h}</span>
                    <span class="pred-grade-item">4h: ${g4h}</span>
                    <span class="pred-grade-item">24h: ${g24h}</span>
                </div>
                <div class="pred-meta">
                    <span>${p.source_count || 1} source${(p.source_count || 1) > 1 ? 's' : ''}</span>
                    <span>SEV ${p.severity || '?'}</span>
                    ${p.reasoning ? `<span class="pred-reasoning">${escapeHtml(p.reasoning)}</span>` : ''}
                </div>
                ${sources.length > 0 ? `<div class="pred-sources">${sources.map(s => `<span class="keyword-chip">${escapeHtml(s)}</span>`).join('')}</div>` : ''}
            </div>`;
        });
    } else {
        html += '<div class="empty-state">No predictions yet — run the pipeline or click "Generate" below</div>';
    }

    document.getElementById('content').innerHTML = html;
    updateLastRefresh();
}

function renderGradeBadge(grade, changePct) {
    if (!grade) return '<span class="grade-badge grade-pending">—</span>';
    const cls = {A: 'grade-a', B: 'grade-b', C: 'grade-c', D: 'grade-d', F: 'grade-f'}[grade] || 'grade-pending';
    const chg = changePct != null ? ` (${changePct >= 0 ? '+' : ''}${changePct.toFixed(2)}%)` : '';
    return `<span class="grade-badge ${cls}">${grade}${chg}</span>`;
}

function renderGradeBar(byGrade) {
    const total = (byGrade.A || 0) + (byGrade.B || 0) + (byGrade.C || 0) + (byGrade.D || 0) + (byGrade.F || 0);
    if (total === 0) return '';
    const pct = (n) => ((n || 0) / total * 100).toFixed(0);
    return `<div class="grade-bar-mini">
        <div class="gb-seg grade-a-bg" style="width:${pct(byGrade.A)}%" title="A: ${byGrade.A || 0}"></div>
        <div class="gb-seg grade-b-bg" style="width:${pct(byGrade.B)}%" title="B: ${byGrade.B || 0}"></div>
        <div class="gb-seg grade-c-bg" style="width:${pct(byGrade.C)}%" title="C: ${byGrade.C || 0}"></div>
        <div class="gb-seg grade-d-bg" style="width:${pct(byGrade.D)}%" title="D: ${byGrade.D || 0}"></div>
        <div class="gb-seg grade-f-bg" style="width:${pct(byGrade.F)}%" title="F: ${byGrade.F || 0}"></div>
    </div>`;
}

function formatAccuracy(acc) {
    if (acc == null) return '<span class="flat-text">—</span>';
    const pct = (acc * 100).toFixed(0);
    const cls = acc >= 0.6 ? 'profit' : acc >= 0.4 ? 'flat-text' : 'loss';
    return `<span class="${cls}" style="font-weight:600">${pct}%</span>`;
}


// ═══════════════════════════════════════════════════════════════
//  ANALYSIS TAB
// ═══════════════════════════════════════════════════════════════

async function loadAnalysis() {
    const [sentiment, categories, assets, narratives] = await Promise.all([
        fetchJSON('/api/sentiment-trend'),
        fetchJSON('/api/category-breakdown'),
        fetchJSON('/api/assets'),
        fetchJSON('/api/narratives'),
    ]);

    let html = '';

    // --- Asset selector for price chart ---
    html += '<div class="chart-controls">';
    html += '<div class="control-group">';
    html += '<span class="group-label">Price Asset</span>';
    const assetList = assets || ['BTC', 'ETH', 'SOL'];
    assetList.forEach(a => {
        html += `<button class="ctrl-btn ${priceAsset === a ? 'active' : ''}" data-price-asset="${a}">${a}</button>`;
    });
    html += '</div>';
    html += '</div>';

    // --- Two-column: Sentiment + Category Pie ---
    html += '<div class="grid-2col">';

    // Sentiment trend
    html += '<div>';
    html += '<div class="chart-wrapper"><div class="chart-title">Sentiment Trend &mdash; Daily Event Count &amp; Sentiment</div><div id="sentiment-chart" class="chart-area"></div></div>';
    html += '</div>';

    // Category pie
    html += '<div>';
    html += '<div class="chart-wrapper"><div class="chart-title">Category Distribution</div><div id="category-pie-chart" class="chart-area"></div></div>';
    html += '</div>';

    html += '</div>'; // end grid-2col

    // --- Price chart ---
    html += '<div class="chart-wrapper"><div class="chart-title">Price Chart &mdash; ' + escapeHtml(priceAsset) + '</div><div id="price-chart" class="chart-area"></div></div>';

    // --- Narrative Timeline ---
    html += '<div class="sub-section">';
    html += '<h3 class="section-title">Narrative Timeline</h3>';
    if (narratives && narratives.length > 0) {
        html += renderNarrativeTimeline(narratives);
    } else {
        html += '<div class="empty-state">No narratives to display</div>';
    }
    html += '</div>';

    document.getElementById('content').innerHTML = html;

    // Asset buttons
    document.querySelectorAll('[data-price-asset]').forEach(btn => {
        btn.addEventListener('click', () => {
            priceAsset = btn.dataset.priceAsset;
            loadAnalysis();
        });
    });

    // Render charts
    if (sentiment && sentiment.length > 0) {
        renderSentimentChart(sentiment);
    }
    if (categories && categories.length > 0) {
        renderCategoryPieChart(categories);
    }

    // Fetch and render price chart
    const priceData = await fetchJSON(`/api/prices?asset=${priceAsset}&limit=200`);
    if (priceData && priceData.length > 0) {
        renderPriceChart(priceData);
    } else {
        const el = document.getElementById('price-chart');
        if (el) el.innerHTML = '<div class="empty-state" style="color:var(--chalk-dim)">No price data for ' + escapeHtml(priceAsset) + '</div>';
    }

    updateLastRefresh();
}

function renderSentimentChart(data) {
    const el = document.getElementById('sentiment-chart');
    if (!el) return;

    const dates = data.map(d => d.date);
    const eventCounts = data.map(d => d.event_count);
    const sentiments = data.map(d => d.avg_sentiment);

    const options = {
        series: [
            { name: 'Event Count', type: 'column', data: eventCounts },
            { name: 'Avg Sentiment', type: 'line', data: sentiments }
        ],
        chart: {
            height: 330,
            background: 'transparent',
            toolbar: { show: false },
        },
        stroke: { width: [0, 3], curve: 'smooth' },
        plotOptions: {
            bar: { columnWidth: '60%', borderRadius: 2 }
        },
        colors: ['rgba(168, 200, 160, 0.5)', '#f0e090'],
        fill: {
            opacity: [0.7, 1]
        },
        xaxis: {
            categories: dates,
            labels: {
                style: { colors: '#f5f0dc', fontFamily: 'Courier New, monospace', fontSize: '9px' },
                rotate: -45,
                rotateAlways: dates.length > 10
            },
            axisBorder: { color: 'rgba(248,240,220,0.15)' },
            axisTicks: { color: 'rgba(248,240,220,0.15)' }
        },
        yaxis: [
            {
                title: { text: 'Events', style: { color: '#a8c8a0', fontFamily: 'DM Sans', fontSize: '10px' } },
                labels: { style: { colors: '#a8c8a0', fontFamily: 'Courier New, monospace', fontSize: '10px' } }
            },
            {
                opposite: true,
                title: { text: 'Sentiment', style: { color: '#f0e090', fontFamily: 'DM Sans', fontSize: '10px' } },
                labels: {
                    style: { colors: '#f0e090', fontFamily: 'Courier New, monospace', fontSize: '10px' },
                    formatter: (val) => val.toFixed(2)
                },
                min: -1,
                max: 1
            }
        ],
        grid: {
            borderColor: 'rgba(248,240,220,0.08)',
            strokeDashArray: 3,
        },
        tooltip: { theme: 'dark', shared: true },
        legend: {
            labels: { colors: '#f5f0dc' },
            fontSize: '11px',
            fontFamily: 'DM Sans, sans-serif'
        },
        theme: { mode: 'dark' }
    };

    sentimentChart = new ApexCharts(el, options);
    sentimentChart.render();
}

function renderCategoryPieChart(data) {
    const el = document.getElementById('category-pie-chart');
    if (!el) return;

    const labels = data.map(d => d.category);
    const series = data.map(d => d.count);
    const colors = labels.map(c => getCategoryColor(c));

    const options = {
        series: series,
        chart: {
            type: 'donut',
            height: 330,
            background: 'transparent',
        },
        labels: labels,
        colors: colors,
        stroke: { width: 1, colors: ['#1c2e18'] },
        dataLabels: {
            enabled: true,
            style: { fontSize: '10px', fontFamily: 'Courier New, monospace', colors: ['#f5f0dc'] },
            dropShadow: { enabled: false }
        },
        legend: {
            position: 'bottom',
            labels: { colors: '#f5f0dc' },
            fontSize: '11px',
            fontFamily: 'DM Sans, sans-serif'
        },
        plotOptions: {
            pie: {
                donut: {
                    size: '55%',
                    labels: {
                        show: true,
                        total: {
                            show: true,
                            label: 'Total',
                            color: '#a8c8a0',
                            fontSize: '12px',
                            fontFamily: 'Courier New, monospace'
                        },
                        value: {
                            color: '#f5f0dc',
                            fontSize: '16px',
                            fontFamily: 'Courier New, monospace'
                        }
                    }
                }
            }
        },
        tooltip: { theme: 'dark' },
        theme: { mode: 'dark' }
    };

    categoryPieChart = new ApexCharts(el, options);
    categoryPieChart.render();
}

function renderPriceChart(data) {
    const el = document.getElementById('price-chart');
    if (!el) return;

    const ohlcData = data.map(d => ({
        x: new Date(d.timestamp).getTime(),
        y: [
            parseFloat(d.open),
            parseFloat(d.high),
            parseFloat(d.low),
            parseFloat(d.close)
        ]
    }));

    const volData = data.map(d => ({
        x: new Date(d.timestamp).getTime(),
        y: parseFloat(d.volume || 0),
        fillColor: parseFloat(d.close) >= parseFloat(d.open) ? 'rgba(136,238,136,0.25)' : 'rgba(238,136,136,0.25)'
    }));

    const options = {
        series: [
            { name: 'Price', type: 'candlestick', data: ohlcData },
            { name: 'Volume', type: 'bar', data: volData }
        ],
        chart: {
            type: 'candlestick',
            height: 400,
            background: 'transparent',
            toolbar: { show: true, tools: { download: false } },
            zoom: { enabled: true }
        },
        plotOptions: {
            candlestick: {
                colors: { upward: '#88ee88', downward: '#ee8888' },
                wick: { useFillColor: true }
            },
            bar: { columnWidth: '60%' }
        },
        grid: {
            borderColor: 'rgba(248,240,220,0.1)',
            strokeDashArray: 3,
            xaxis: { lines: { show: false } },
            yaxis: { lines: { show: true } }
        },
        xaxis: {
            type: 'datetime',
            labels: {
                style: { colors: '#f5f0dc', fontFamily: 'Courier New, monospace', fontSize: '10px' }
            },
            axisBorder: { color: 'rgba(248,240,220,0.15)' },
            axisTicks: { color: 'rgba(248,240,220,0.15)' }
        },
        yaxis: [
            {
                seriesName: 'Price',
                labels: {
                    style: { colors: '#f5f0dc', fontFamily: 'Courier New, monospace', fontSize: '10px' },
                    formatter: (val) => val.toFixed(2)
                },
                tooltip: { enabled: true }
            },
            {
                seriesName: 'Volume',
                opposite: true,
                labels: {
                    style: { colors: '#a8c8a0', fontFamily: 'Courier New, monospace', fontSize: '10px' },
                    formatter: (val) => formatCompactNumber(val)
                },
                max: (max) => max * 4
            }
        ],
        tooltip: {
            theme: 'dark',
            shared: false,
            custom: function({ seriesIndex, dataPointIndex, w }) {
                if (seriesIndex === 0) {
                    const o = w.globals.seriesCandleO[seriesIndex][dataPointIndex];
                    const h = w.globals.seriesCandleH[seriesIndex][dataPointIndex];
                    const l = w.globals.seriesCandleL[seriesIndex][dataPointIndex];
                    const c = w.globals.seriesCandleC[seriesIndex][dataPointIndex];
                    const time = new Date(w.globals.seriesX[seriesIndex][dataPointIndex]);
                    return `
                        <div style="padding:8px 12px;font-family:Courier New,monospace;font-size:12px;background:#1c2e18;border:1px solid #3a5030;color:#f5f0dc">
                            <div style="color:#a8c8a0;margin-bottom:4px">${time.toLocaleString()}</div>
                            <div>O: <span style="color:#f5f0dc">${o.toFixed(2)}</span></div>
                            <div>H: <span style="color:#f5f0dc">${h.toFixed(2)}</span></div>
                            <div>L: <span style="color:#f5f0dc">${l.toFixed(2)}</span></div>
                            <div>C: <span style="color:${c >= o ? '#88ee88' : '#ee8888'}">${c.toFixed(2)}</span></div>
                        </div>
                    `;
                }
                return '';
            }
        },
        theme: { mode: 'dark' }
    };

    priceChart = new ApexCharts(el, options);
    priceChart.render();
}

function renderNarrativeTimeline(narratives) {
    if (!narratives || narratives.length === 0) return '<div class="empty-state">No narratives</div>';

    // Find global date range
    let minDate = null;
    let maxDate = null;
    narratives.forEach(n => {
        const first = new Date(n.first_seen);
        const last = new Date(n.last_seen);
        if (!minDate || first < minDate) minDate = first;
        if (!maxDate || last > maxDate) maxDate = last;
    });

    if (!minDate || !maxDate) return '<div class="empty-state">Invalid date range</div>';

    const totalMs = maxDate.getTime() - minDate.getTime();
    if (totalMs <= 0) return '<div class="empty-state">All narratives on same date</div>';

    let html = '<div style="padding:8px 0">';
    narratives.sort((a, b) => (b.event_count || 0) - (a.event_count || 0));

    narratives.forEach(n => {
        const first = new Date(n.first_seen);
        const last = new Date(n.last_seen);
        const leftPct = ((first.getTime() - minDate.getTime()) / totalMs * 100).toFixed(1);
        const widthPct = Math.max(2, ((last.getTime() - first.getTime()) / totalMs * 100)).toFixed(1);

        html += `
            <div class="timeline-bar">
                <div class="timeline-label" title="${escapeHtml(n.name)}">${escapeHtml(truncate(n.name, 22))}</div>
                <div class="timeline-track">
                    <div class="timeline-fill" style="left:${leftPct}%;width:${widthPct}%">
                        <span class="timeline-fill-text">${n.event_count} events</span>
                    </div>
                </div>
            </div>
        `;
    });

    // Date axis labels
    html += `
        <div class="timeline-dates" style="margin-left:148px">
            <span>${minDate.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</span>
            <span>${maxDate.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</span>
        </div>
    `;

    html += '</div>';
    return html;
}

// ═══════════════════════════════════════════════════════════════
//  PROJECTS TAB
// ═══════════════════════════════════════════════════════════════

async function loadProjects() {
    const [stats, pipeline, status] = await Promise.all([
        fetchJSON('/api/stats'),
        fetchJSON('/api/pipeline-status'),
        fetchJSON('/api/status'),
    ]);

    let html = '<h2 class="section-title">Project Status</h2>';

    // This project card
    html += '<div class="project-grid">';

    html += `
        <div class="project-card">
            <div class="project-card-header">
                <span class="project-name">Crypto News Engine</span>
                <div style="display:flex;align-items:center;gap:6px">
                    <span class="status-dot ${status && status.online ? 'online' : 'offline'}"></span>
                    <span style="font-size:0.75rem;color:var(--text-muted)">${status && status.online ? 'Online' : 'Offline'}</span>
                </div>
            </div>
            <div class="project-stats">
                <div class="project-stat">
                    <span class="p-label">Articles</span>
                    <span class="p-value">${stats ? stats.articles : '--'}</span>
                </div>
                <div class="project-stat">
                    <span class="p-label">Events</span>
                    <span class="p-value">${stats ? stats.events : '--'}</span>
                </div>
                <div class="project-stat">
                    <span class="p-label">Signals (v2)</span>
                    <span class="p-value">${stats ? stats.signals : '--'}</span>
                </div>
                <div class="project-stat">
                    <span class="p-label">DB Size</span>
                    <span class="p-value">${status ? status.db_size_mb + ' MB' : '--'}</span>
                </div>
                <div class="project-stat">
                    <span class="p-label">Last Ingestion</span>
                    <span class="p-value text-muted" style="font-size:0.8rem">${status ? timeAgo(status.last_ingestion) : '--'}</span>
                </div>
                <div class="project-stat">
                    <span class="p-label">Dashboard Up Since</span>
                    <span class="p-value text-muted" style="font-size:0.8rem">${status ? timeAgo(status.started_at) : '--'}</span>
                </div>
            </div>
        </div>
    `;

    // Reporter / Central Hub card
    html += `
        <div class="project-card">
            <div class="project-card-header">
                <span class="project-name">Central Hub (Reporter)</span>
                <div style="display:flex;align-items:center;gap:6px">
                    <span class="status-dot degraded"></span>
                    <span style="font-size:0.75rem;color:var(--text-muted)">Standby</span>
                </div>
            </div>
            <div class="project-stats">
                <div class="project-stat">
                    <span class="p-label">Role</span>
                    <span class="p-value">Data Reporter</span>
                </div>
                <div class="project-stat">
                    <span class="p-label">Reports To</span>
                    <span class="p-value">Futures Hub</span>
                </div>
                <div class="project-stat">
                    <span class="p-label">Data Sent</span>
                    <span class="p-value text-muted" style="font-size:0.8rem">Signals, Narratives</span>
                </div>
                <div class="project-stat">
                    <span class="p-label">Status</span>
                    <span class="p-value text-muted" style="font-size:0.8rem">Configured</span>
                </div>
            </div>
        </div>
    `;

    html += '</div>';

    // Pipeline log section
    html += '<div class="sub-section mt-2">';
    html += '<h3 class="section-title">Pipeline Status</h3>';

    if (pipeline) {
        const statusColor = pipeline.status === 'ok' ? 'profit' : 'text-muted';
        html += `<div style="margin-bottom:12px"><span class="${statusColor}" style="font-weight:700">Status: ${pipeline.status.toUpperCase()}</span>`;
        if (pipeline.last_run) {
            html += ` &mdash; Last run: ${formatTime(pipeline.last_run)}`;
        }
        html += '</div>';

        if (pipeline.stats) {
            const s = pipeline.stats;
            html += '<div class="roster-summary" style="max-width:500px">';
            if (s.articles_processed != null) html += renderStatCard(s.articles_processed, 'Processed');
            if (s.events_created != null) html += renderStatCard(s.events_created, 'Events');
            if (s.clusters_created != null) html += renderStatCard(s.clusters_created, 'Clusters');
            html += '</div>';
        }

        if (pipeline.log_tail && pipeline.log_tail.length > 0) {
            html += '<div style="background:var(--board-bg);padding:12px;border-radius:4px;margin-top:12px;font-family:Courier New,monospace;font-size:11px;color:var(--chalk-dim);max-height:200px;overflow-y:auto;white-space:pre-wrap">';
            pipeline.log_tail.forEach(line => {
                html += escapeHtml(line) + '\n';
            });
            html += '</div>';
        }
    } else {
        html += '<div class="empty-state">Could not load pipeline status</div>';
    }
    html += '</div>';

    document.getElementById('content').innerHTML = html;
    updateLastRefresh();
}

// ═══════════════════════════════════════════════════════════════
//  HEADER: Status & Clock
// ═══════════════════════════════════════════════════════════════

async function fetchHeaderStatus() {
    try {
        const [status, pipeline] = await Promise.all([
            fetchJSON('/api/status'),
            fetchJSON('/api/pipeline-status'),
        ]);

        const dot = document.getElementById('status-dot');
        const label = document.getElementById('status-label');
        const pipelineEl = document.getElementById('pipeline-status');

        if (status && status.online) {
            dot.className = 'status-dot online';
            label.textContent = 'Online';
        } else {
            dot.className = 'status-dot offline';
            label.textContent = 'Offline';
        }

        if (pipeline && pipeline.status === 'ok' && pipeline.last_run) {
            pipelineEl.textContent = 'Pipeline: ' + timeAgo(pipeline.last_run);
        } else {
            pipelineEl.textContent = 'Pipeline: --';
        }
    } catch (err) {
        // Silently fail
    }
}

function updateClock() {
    const now = new Date();
    const h = String(now.getHours()).padStart(2, '0');
    const m = String(now.getMinutes()).padStart(2, '0');
    const s = String(now.getSeconds()).padStart(2, '0');
    document.getElementById('clock').textContent = `${h}:${m}:${s}`;
}

// ─── Auto Refresh ──────────────────────────────────────────────
function startAutoRefresh() {
    if (refreshInterval) clearInterval(refreshInterval);
    refreshInterval = setInterval(() => {
        loadTab(currentTab);
    }, REFRESH_MS);
}

function updateLastRefresh() {
    const now = new Date();
    const timeStr = now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const el = document.getElementById('last-refresh');
    if (el) el.textContent = timeStr;
}

// ─── Chart Cleanup ─────────────────────────────────────────────
function destroyAllCharts() {
    if (categoryChart) { categoryChart.destroy(); categoryChart = null; }
    if (sentimentChart) { sentimentChart.destroy(); sentimentChart = null; }
    if (priceChart) { priceChart.destroy(); priceChart = null; }
    if (categoryPieChart) { categoryPieChart.destroy(); categoryPieChart = null; }
}

// ═══════════════════════════════════════════════════════════════
//  UTILITIES: Formatting
// ═══════════════════════════════════════════════════════════════

function formatPrice(n) {
    if (n == null || isNaN(n)) return '--';
    return parseFloat(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatTime(iso) {
    if (!iso) return '--';
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '--';
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' +
               d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
    } catch {
        return '--';
    }
}

function timeAgo(iso) {
    if (!iso) return '--';
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '--';
        const diffMs = Date.now() - d.getTime();
        const diffSec = Math.floor(diffMs / 1000);
        if (diffSec < 0) return 'just now';
        if (diffSec < 60) return `${diffSec}s ago`;
        const diffMin = Math.floor(diffSec / 60);
        if (diffMin < 60) return `${diffMin}m ago`;
        const diffHr = Math.floor(diffMin / 60);
        if (diffHr < 24) return `${diffHr}h ago`;
        const diffDay = Math.floor(diffHr / 24);
        return `${diffDay}d ago`;
    } catch {
        return '--';
    }
}

function formatCompactNumber(n) {
    if (n == null) return '0';
    if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return n.toString();
}

function truncate(str, max) {
    if (!str) return '';
    return str.length > max ? str.substring(0, max) + '...' : str;
}

// ═══════════════════════════════════════════════════════════════
//  UTILITIES: Helpers
// ═══════════════════════════════════════════════════════════════

function formatNum(n) {
    if (n == null) return '--';
    if (Math.abs(n) >= 1000) return n.toLocaleString('en-US', { maximumFractionDigits: 0 });
    if (Math.abs(n) >= 1) return n.toFixed(2);
    return n.toFixed(4);
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

async function fetchJSON(url) {
    try {
        const resp = await fetch(url);
        if (!resp.ok) {
            console.error(`API error: ${resp.status} for ${url}`);
            return null;
        }
        return await resp.json();
    } catch (err) {
        console.error(`Fetch failed for ${url}:`, err);
        return null;
    }
}

function getCategoryColor(cat) {
    const colors = {
        'REGULATORY': '#4285f4',
        'EXCHANGE': '#a050dc',
        'PROTOCOL': '#50b450',
        'MACRO': '#e6a032',
        'ADOPTION': '#28b4b4',
        'SENTIMENT': '#d4c850',
        'SECURITY': '#dc5050',
        'MARKET_STRUCTURE': '#8c8c8c',
    };
    return colors[cat] || '#b89848';
}

function getSourceClass(source) {
    if (!source) return 'source-default';
    const s = source.toLowerCase();
    if (s.includes('coindesk')) return 'source-coindesk';
    if (s.includes('cointelegraph')) return 'source-cointelegraph';
    if (s.includes('decrypt')) return 'source-decrypt';
    if (s.includes('the block')) return 'source-theblock';
    if (s.includes('bitcoin magazine')) return 'source-bitcoinmagazine';
    if (s.startsWith('gdelt/')) return 'source-gdelt';
    return 'source-default';
}

function getSeverityClass(severity) {
    const sev = parseInt(severity);
    if (sev >= 4) return 'sev-high';
    if (sev === 3) return 'sev-medium';
    return 'sev-low';
}
