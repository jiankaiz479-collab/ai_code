(() => {
    const historyGrid = document.getElementById('history-grid');
    const emptyState = document.getElementById('empty-state');
    const resultCount = document.getElementById('result-count');
    const currentFilterLabel = document.getElementById('current-filter-label');
    const modal = document.getElementById('history-modal');
    const modalImg = document.getElementById('modal-img');
    const modalType = document.getElementById('modal-type');
    const modalStatus = document.getElementById('modal-status');
    const modalStart = document.getElementById('modal-start');
    const modalEnd = document.getElementById('modal-end');
    const modalTime = document.getElementById('modal-time');
    const modalResult = document.getElementById('modal-result');
    const modalJson = document.getElementById('modal-json');
    const filterButtons = Array.from(document.querySelectorAll('.filter-button'));

    const operationLabels = {
        all: '全部歷史',
        remove_bg: '去背',
        tryon_2d: '2D',
        reconstruct_3d: '3D',
    };

    const mockRecords = [
        {
            id: 1,
            filter: 'remove_bg',
            type: '去背',
            status: 'success',
            title: '白襯衫背景清除',
            subtitle: '衣物輪廓清楚，背景已順利移除。',
            createdAt: '2026-05-15 09:42',
            start: '2026-05-15 09:41:22',
            end: '2026-05-15 09:41:31',
            duration: '9.2 s',
            result: '輸出透明背景 PNG',
            image: makeCardImage('#2563eb', '#dbeafe', 'BG'),
            largeImage: makeLargeImage('#0f172a', '#60a5fa', 'REMOVE BG'),
            response: {
                code: 200,
                message: '1200',
                data: {
                    file_name: 'shirt_no_bg.png',
                    style_analysis: 'clean-cut',
                },
            },
        },
        {
            id: 2,
            filter: 'tryon_2d',
            type: '2D',
            status: 'success',
            title: '上衣試穿合成',
            subtitle: '模特與衣服順利對齊，輸出結果正常。',
            createdAt: '2026-05-15 10:18',
            start: '2026-05-15 10:17:48',
            end: '2026-05-15 10:18:21',
            duration: '32.8 s',
            result: '生成試穿合成圖',
            image: makeCardImage('#7c3aed', '#ede9fe', '2D'),
            largeImage: makeLargeImage('#4c1d95', '#c4b5fd', 'TRY ON 2D'),
            response: {
                code: 200,
                message: '2200',
                data: {
                    file_name: 'tryon_2d.png',
                    style_name: 'casual',
                    file_format: 'PNG',
                },
            },
        },
        {
            id: 3,
            filter: 'reconstruct_3d',
            type: '3D',
            status: 'success',
            title: '人物 3D 重建',
            subtitle: '完成 GLB 輸出，可切換多個觀看角度。',
            createdAt: '2026-05-15 11:03',
            start: '2026-05-15 11:01:57',
            end: '2026-05-15 11:03:06',
            duration: '68.9 s',
            result: '輸出 3D 模型檔',
            image: makeCardImage('#059669', '#d1fae5', '3D'),
            largeImage: makeLargeImage('#064e3b', '#6ee7b7', '3D MODEL'),
            response: {
                code: 200,
                message: '4200',
                data: {
                    glb_file: 'person.glb',
                    texture_aligned: true,
                },
            },
        },
        {
            id: 4,
            filter: 'remove_bg',
            type: '去背',
            status: 'failed',
            title: '半身照去背失敗',
            subtitle: '主體不完整，觸發品質檢查錯誤。',
            createdAt: '2026-05-15 11:34',
            start: '2026-05-15 11:34:01',
            end: '2026-05-15 11:34:05',
            duration: '4.1 s',
            result: '請上傳更完整的人像',
            image: makeCardImage('#dc2626', '#fee2e2', 'ERR'),
            largeImage: makeLargeImage('#7f1d1d', '#fecaca', 'FAILED'),
            response: {
                code: 422,
                message: '1422',
                debug_info: {
                    ui_behavior: '請上傳比例正常的圖片',
                },
            },
        },
        {
            id: 5,
            filter: 'tryon_2d',
            type: '2D',
            status: 'success',
            title: '裙裝試穿版本 B',
            subtitle: '衣服紋理對齊更穩定，邊界也更自然。',
            createdAt: '2026-05-15 12:07',
            start: '2026-05-15 12:06:12',
            end: '2026-05-15 12:06:39',
            duration: '27.4 s',
            result: '生成完整合成圖',
            image: makeCardImage('#f59e0b', '#fef3c7', '2D'),
            largeImage: makeLargeImage('#92400e', '#fde68a', 'TRY ON B'),
            response: {
                code: 200,
                message: '2200',
                data: {
                    file_name: 'tryon_dress.png',
                    style_name: 'formal',
                    file_format: 'PNG',
                },
            },
        },
        {
            id: 6,
            filter: 'reconstruct_3d',
            type: '3D',
            status: 'failed',
            title: '3D 模型紋理錯位',
            subtitle: '模型有生成，但材質對齊需要再調整。',
            createdAt: '2026-05-15 12:42',
            start: '2026-05-15 12:41:20',
            end: '2026-05-15 12:42:18',
            duration: '58.7 s',
            result: '紋理對齊失敗',
            image: makeCardImage('#14b8a6', '#ccfbf1', '3D'),
            largeImage: makeLargeImage('#134e4a', '#99f6e4', 'ALIGN ERR'),
            response: {
                code: 500,
                message: '4500',
                debug_info: {
                    error_detail: 'texture alignment failed',
                },
            },
        },
    ];

    let activeFilter = 'all';

    function makeCardImage(background, accent, label) {
        const svg = `
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 320" role="img" aria-label="${label}">
                <defs>
                    <linearGradient id="g" x1="0" x2="1" y1="0" y2="1">
                        <stop offset="0%" stop-color="${background}"/>
                        <stop offset="100%" stop-color="${accent}"/>
                    </linearGradient>
                </defs>
                <rect width="320" height="320" fill="url(#g)"/>
                <circle cx="118" cy="122" r="62" fill="rgba(255,255,255,0.7)"/>
                <rect x="92" y="158" width="132" height="96" rx="32" fill="rgba(255,255,255,0.74)"/>
                <text x="160" y="275" fill="#0f172a" font-size="58" font-weight="700" text-anchor="middle" font-family="Arial, sans-serif">${label}</text>
            </svg>`;
        return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
    }

    function makeLargeImage(background, accent, label) {
        const svg = `
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 720" role="img" aria-label="${label}">
                <defs>
                    <linearGradient id="g2" x1="0" x2="1" y1="0" y2="1">
                        <stop offset="0%" stop-color="${background}"/>
                        <stop offset="100%" stop-color="${accent}"/>
                    </linearGradient>
                </defs>
                <rect width="960" height="720" rx="32" fill="url(#g2)"/>
                <rect x="150" y="96" width="660" height="528" rx="36" fill="rgba(255,255,255,0.24)" stroke="rgba(255,255,255,0.42)"/>
                <circle cx="360" cy="260" r="96" fill="rgba(255,255,255,0.72)"/>
                <rect x="260" y="320" width="440" height="198" rx="56" fill="rgba(255,255,255,0.8)"/>
                <text x="480" y="628" fill="#ffffff" font-size="74" font-weight="700" text-anchor="middle" font-family="Arial, sans-serif">${label}</text>
            </svg>`;
        return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
    }

    function formatResponse(response) {
        return JSON.stringify(response, null, 2);
    }

    function renderCards() {
        const records = activeFilter === 'all'
            ? mockRecords
            : mockRecords.filter((record) => record.filter === activeFilter);

        historyGrid.innerHTML = '';
        resultCount.textContent = String(records.length);
        currentFilterLabel.textContent = operationLabels[activeFilter] || '全部歷史';

        emptyState.classList.toggle('hidden', records.length !== 0);

        records.forEach((record) => {
            const card = document.createElement('article');
            card.className = 'history-card';
            card.tabIndex = 0;
            card.setAttribute('role', 'button');
            card.setAttribute('aria-label', `${record.title}，點擊查看詳情`);
            card.dataset.recordId = String(record.id);

            card.innerHTML = `
                <div class="card-image-wrap">
                    <img src="${record.image}" alt="${record.title}">
                    <span class="card-badge">${record.type}</span>
                    <span class="status-chip ${record.status}">${record.status === 'success' ? '成功' : '失敗'}</span>
                </div>
                <div class="card-body">
                    <div class="card-title-row">
                        <h3 class="card-title">${record.title}</h3>
                    </div>
                    <p class="card-subtitle">${record.subtitle}</p>
                    <div class="card-meta">
                        <div class="card-meta-row"><span>時間</span><strong>${record.createdAt}</strong></div>
                        <div class="card-meta-row"><span>耗時</span><strong>${record.duration}</strong></div>
                        <div class="card-meta-row"><span>結果</span><strong>${record.result}</strong></div>
                    </div>
                </div>
            `;

            card.addEventListener('click', () => openModal(record));
            card.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    openModal(record);
                }
            });

            historyGrid.appendChild(card);
        });
    }

    function openModal(record) {
        modalImg.src = record.largeImage;
        modalImg.alt = record.title;
        modalType.textContent = record.type;
        modalStatus.textContent = record.status === 'success' ? '成功' : '失敗';
        modalStatus.className = `status-badge ${record.status}`;
        modalStart.textContent = record.start;
        modalEnd.textContent = record.end;
        modalTime.textContent = record.duration;
        modalResult.textContent = record.result;
        modalJson.textContent = formatResponse(record.response);
        modal.classList.remove('hidden');
        document.body.style.overflow = 'hidden';
        document.querySelector('.close-btn')?.focus();
    }

    function closeModal() {
        modal.classList.add('hidden');
        document.body.style.overflow = '';
    }

    filterButtons.forEach((button) => {
        button.addEventListener('click', () => {
            filterButtons.forEach((item) => item.classList.remove('active'));
            button.classList.add('active');
            activeFilter = button.dataset.filter || 'all';
            renderCards();
        });
    });

    modal.addEventListener('click', (event) => {
        if (event.target.matches('[data-close-modal]')) {
            closeModal();
        }
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
            closeModal();
        }
    });

    renderCards();
})();