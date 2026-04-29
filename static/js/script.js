// Redirection logic for localhost
if (window.location.hostname === '127.0.0.1') {
    window.location.href = window.location.href.replace('127.0.0.1', 'localhost');
}

let token = localStorage.getItem('token');
let assistantsList = [];
let isDarkTheme = localStorage.getItem('theme') === 'dark';
let pendingFiles = [];
let pendingModalFiles = [];
let currentAudio = null;
let currentEditId = null;

// Initialize persistent Chat Session ID (Shared across all agents in this browser session)
if (!sessionStorage.getItem('chat_session_id')) {
    sessionStorage.setItem('chat_session_id', 'chat_' + Math.random().toString(36).substr(2, 9));
}
const chatSessionId = sessionStorage.getItem('chat_session_id');


// Apply saved theme immediately
if (isDarkTheme) document.documentElement.setAttribute('data-theme', 'dark');

// SIDEBAR RESPONSIVE CONTROLS
function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
    document.getElementById('sidebarOverlay').classList.toggle('active');
}
function closeSidebar() {
    document.getElementById('sidebar').classList.remove('open');
    document.getElementById('sidebarOverlay').classList.remove('active');
}

// VOICES - Professional Selection
const DEFAULT_VOICE_ID = 'Hp07ONf6C5qlCKOeB4oo'; // Constantin Birkedal

async function fetchVoices() {
    try {
        // Fetch from Vapi (primary) and ElevenLabs (for preview URLs) in parallel
        const [vapiRes, elRes] = await Promise.allSettled([
            apiCall("/api/vapi-voices"),
            fetch("https://api.elevenlabs.io/v1/voices").then(r => r.json())
        ]);

        const vapiData = vapiRes.status === 'fulfilled' ? await vapiRes.value.json() : [];
        const elData = elRes.status === 'fulfilled' ? elRes.value.voices : [];

        const select = document.getElementById("voiceId");
        if (!select) return;
        select.innerHTML = '';

        const vapiVoices = Array.isArray(vapiData) ? vapiData : (vapiData.voices || []);
        
        // Merge lists: Start with Vapi voices, then add all ElevenLabs voices
        const mergedVoices = [...vapiVoices];
        const existingIds = new Set(vapiVoices.map(v => v.id || v.voiceId));
        
        if (elData) {
            elData.forEach(ev => {
                if (!existingIds.has(ev.voice_id)) {
                    mergedVoices.push({
                        id: ev.voice_id,
                        name: ev.name,
                        provider: '11labs',
                        previewUrl: ev.preview_url
                    });
                }
            });
        }

        // Map ElevenLabs previews by ID for enrichment of Vapi records
        // Map ElevenLabs previews by ID for enrichment of Vapi records
        const elPreviews = {};
        if (elData) elData.forEach(v => { elPreviews[v.voice_id] = v.preview_url; });

        const targetVoiceId = 'Hp07ONf6C5qlCKOeB4oo';
        const targetVoice = mergedVoices.find(v => (v.id === targetVoiceId || v.voiceId === targetVoiceId) || (v.name && (v.name.toLowerCase().includes('constantin') || v.name.toLowerCase().includes('birkedal'))));
        const actualTargetId = targetVoice ? (targetVoice.id || targetVoice.voiceId) : targetVoiceId;

        const otherVoices = mergedVoices.filter(v => (v.id !== actualTargetId && v.voiceId !== actualTargetId));
        
        const sorted = [];
        if (targetVoice) sorted.push(targetVoice);
        sorted.push(...otherVoices);

        sorted.forEach(v => {
            const vid = v.id || v.voiceId;
            const preview = v.previewUrl || v.preview_url || elPreviews[vid] || '';
            const opt = document.createElement('option');
            opt.value = vid;
            opt.text = (vid === actualTargetId ? '⭐ ' : '') + v.name + ` (${v.provider || 'vapi'})`;
            opt.dataset.preview = preview;
            select.appendChild(opt);
        });

        if (targetVoice) {
            select.value = actualTargetId;
        } else if (sorted.length > 0) {
            select.value = (sorted[0].id || sorted[0].voiceId);
        }
    } catch (e) {
        console.error("Error fetching voices", e);
        const select = document.getElementById("voiceId");
        if (select) select.innerHTML = '<option value="Hp07ONf6C5qlCKOeB4oo">Constantin Birkedal (Default)</option>';
    }
}
const fetchElevenLabsVoices = fetchVoices;

function playSelectedVoice() {
    const select = document.getElementById("voiceId");
    const selectedOpt = select.options[select.selectedIndex];
    const btn = document.getElementById('playVoiceBtn');
    if (!selectedOpt || selectedOpt.disabled) return;
    const url = selectedOpt.dataset.preview;
    if (!url) { Swal.fire('No Preview', 'This voice does not have a preview URL.', 'info'); return; }
    if (currentAudio) {
        currentAudio.pause(); currentAudio.currentTime = 0;
        if (currentAudio._srcUrl === url) { currentAudio = null; btn.innerHTML = '▶️'; return; }
    }
    currentAudio = new Audio(url);
    currentAudio._srcUrl = url;
    currentAudio.play();
    btn.innerHTML = '⏸️';
    currentAudio.onended = () => { btn.innerHTML = '▶️'; currentAudio = null; };
}

// FILE HANDLING
function updatePendingFilesUI() {
    const previewDiv = document.getElementById('filePreview');
    if (!previewDiv) return;
    previewDiv.innerHTML = pendingFiles.map((file, idx) => `
    <div style="display:inline-flex;align-items:center;background:var(--secondary);padding:.35rem .6rem;border-radius:1rem;font-size:.8rem;margin:.2rem;">
        📄 ${file.name}
        <span style="cursor:pointer;color:var(--danger);margin-left:.4rem;font-weight:bold;" onclick="removePendingFile(${idx})">✖</span>
    </div>
`).join('');
}
function handleFileSelection(e) {
    for (let i = 0; i < e.target.files.length; i++) pendingFiles.push(e.target.files[i]);
    updatePendingFilesUI();
    e.target.value = '';
}
function removePendingFile(idx) { pendingFiles.splice(idx, 1); updatePendingFilesUI(); }

// Modal-specific file helpers
function updateModalFilesUI() {
    const previewDiv = document.getElementById('modalFilePreview');
    if (!previewDiv) return;
    previewDiv.innerHTML = pendingModalFiles.map((file, idx) => `
    <div style="display:inline-flex;align-items:center;background:var(--secondary);padding:.35rem .6rem;border-radius:1rem;font-size:.8rem;margin:.2rem;">
        📄 ${file.name}
        <span style="cursor:pointer;color:var(--danger);margin-left:.4rem;font-weight:bold;" onclick="removeModalFile(${idx})">✖</span>
    </div>
`).join('');
}
function handleModalFileSelection(e) {
    for (let i = 0; i < e.target.files.length; i++) pendingModalFiles.push(e.target.files[i]);
    updateModalFilesUI();
    e.target.value = '';
}
function removeModalFile(idx) { pendingModalFiles.splice(idx, 1); updateModalFilesUI(); }

// AUTH
async function doLogin() {
    const username = document.getElementById('loginUsername').value;
    const password = document.getElementById('loginPassword').value;
    const btn = document.querySelector('#loginPage .btn-primary');
    const origText = btn.innerHTML; btn.innerHTML = 'Signing In...'; btn.disabled = true;
    try {
        const res = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) });
        if (res.ok) {
            const data = await res.json();
            token = data.access_token;
            localStorage.setItem('token', token);
            document.getElementById('loginPage').style.display = 'none';
            document.getElementById('appContainer').style.display = 'block';
            initDashboard();
        } else {
            document.getElementById('loginError').innerText = 'Invalid credentials. Please try again.';
        }
    } catch (e) {
        document.getElementById('loginError').innerText = 'Network error. Make sure server is running.';
    } finally {
        btn.innerHTML = origText; btn.disabled = false;
    }
}
function logout() { localStorage.removeItem('token'); location.reload(); }


async function apiCall(url, options = {}) {
    const headers = { 'Authorization': `Bearer ${token}`, ...options.headers };
    const res = await fetch(url, { ...options, headers });
    if (res.status === 401) { logout(); throw new Error('Session expired'); }
    return res;
}

// CREATE ASSISTANT
async function createAssistant(e) {
    e.preventDefault();
    const btn = e.target.querySelector('button[type="submit"]');
    btn.disabled = true;
    Swal.fire({ title: 'Building AI Agent... ✨', html: 'Sit tight! Configuring your agent.<br><br>🤖 ⚙️ ⚡', allowOutsideClick: false, showConfirmButton: false, didOpen: () => Swal.showLoading() });
    const fd = new FormData();
    fd.append('assistant_name', document.getElementById('name').value);
    fd.append('model', document.getElementById('model').value);
    fd.append('voice_id', document.getElementById('voiceId').value);
    fd.append('language', document.getElementById('language').value);
    const prompt = document.getElementById('systemPrompt').value.trim();
    if (prompt) fd.append('system_prompt', prompt);
    for (let i = 0; i < pendingFiles.length; i++) fd.append('files', pendingFiles[i]);
    try {
        const res = await apiCall('/api/create-assistant', { method: 'POST', body: fd });
        const data = await res.json();
        if (data.success) {
            Swal.fire({ icon: 'success', title: 'Agent Created! 🎉', text: 'Your new AI agent is live on Pizzeria Network.', timer: 2000, showConfirmButton: false });
            document.getElementById('createForm').reset();
            pendingFiles = []; updatePendingFilesUI();
            loadAssistants();
            setTimeout(() => switchTab('manage'), 1500);
        } else {
            Swal.fire('Error', data.error || 'Failed to create agent', 'error');
        }
    } catch (err) {
        Swal.fire('Error', 'Error calling API.', 'error');
    } finally { btn.disabled = false; }
}

// LOAD ASSISTANTS (grid view)
async function loadAssistants() {
    const grid = document.getElementById('agentsGrid');
    if (!grid) return;
    grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:2rem;color:var(--text-muted);">Loading agents...</div>';
    try {
        const res = await apiCall('/api/assistants');
        const data = await res.json();
        assistantsList = data.assistants;
        if (!data.total) {
            grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:4rem 2rem;background:var(--card-bg);border:1px dashed var(--border);border-radius:var(--radius-lg);">
            <div style="font-size:3rem;margin-bottom:1rem;">🤖</div>
            <h3 style="color:var(--text-main);margin-bottom:.5rem;">No Agents Found</h3>
            <p style="color:var(--text-muted);margin-bottom:1.5rem;">Create your first AI voice agent to get started.</p>
            <button class="btn btn-primary" onclick="switchTab('create')">Create Agent</button>
        </div>`;
            loadChatSelect(); updateFilterSelect();
            return;
        }
        grid.innerHTML = assistantsList.map(a => `
        <div class="assistant-card" onclick="openAgentDetail('${a.id}')" title="Click to manage agent">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <h3 style="margin:0;">🤖 ${a.name}</h3>
                <span style="font-size:0.6rem;font-weight:700;padding:2px 6px;border-radius:10px;background:#a855f720;color:#a855f7;text-transform:uppercase;">🌐 ${a.language === 'da' ? 'DANISH' : 'ENGLISH'}</span>
            </div>
            <div class="card-meta-row">
                <span class="assistant-meta">🧠 ${
                a.model === 'gpt-4o-mini' ? 'GPT-4o Mini' :
                a.model === 'gpt-4o' ? 'GPT-4o' :
                a.model === 'llama-3.3-70b-versatile' ? '⚡ Llama 3.3 70B' :
                a.model === 'llama-3.1-8b-instant' ? '⚡ Llama 3.1 8B' :
                a.model
            }</span>
                <span class="assistant-meta">🎙️ Voice configured</span>
            </div>
            <div class="card-prompt-preview">${(a.system_prompt || '').substring(0, 150) || 'No system prompt'}</div>
            <div class="flex-actions" onclick="event.stopPropagation()">
                <button class="btn btn-small btn-danger" onclick="deleteAssistant('${a.id}')" style="margin-left:auto;" title="Delete Agent">🗑️ Delete</button>
            </div>
        </div>
    `).join('');
        loadChatSelect(); updateFilterSelect();
    } catch (e) {
        grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:2rem;color:var(--danger);">Error loading agents.</div>';
    }
}

// AGENT DETAIL MODAL — Edit + KB Management in one modal
async function openAgentDetail(id) {
    currentEditId = id;
    const modal = document.getElementById('agentDetailModal');
    const body = document.getElementById('agentDetailBody');
    document.getElementById('agentDetailTitle').textContent = 'Loading...';
    body.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--text-muted);">Loading agent details...</div>';
    modal.classList.add('active');

    try {
        const res = await apiCall(`/api/assistant/${id}`);
        const a = await res.json();
        document.getElementById('agentDetailTitle').textContent = `🤖 ${a.name}`;

        const voiceSelectHtml = await buildVoiceSelectHtml(a.voice_id);

        const fileRows = (a.files || []).map(f => `
            <div style="display:flex;align-items:center;justify-content:space-between;padding:.6rem .75rem;background:var(--bg-color);border:1px solid var(--border);border-radius:var(--radius-md);margin-bottom:.4rem;">
                <span style="font-size:.85rem;">📄 ${escapeHtml(f.name)}</span>
                <button class="btn btn-small btn-danger" onclick="deleteKbFileInModal('${id}','${f.vapi_file_id}')">🗑️</button>
            </div>`).join('');

        body.innerHTML = `
        <!-- TABS -->
        <div style="display:flex;gap:0;border-bottom:2px solid var(--border);margin-bottom:1.25rem;">
            <button id="tab-edit-btn" onclick="switchAgentTab('edit')" style="flex:1;padding:.6rem;border:none;background:none;cursor:pointer;font-weight:600;font-size:.9rem;color:var(--primary);border-bottom:2px solid var(--primary);margin-bottom:-2px;">✏️ Edit Agent</button>
            <button id="tab-kb-btn" onclick="switchAgentTab('kb')" style="flex:1;padding:.6rem;border:none;background:none;cursor:pointer;font-weight:600;font-size:.9rem;color:var(--text-muted);">📂 Knowledge Base</button>
        </div>

        <!-- EDIT TAB -->
        <div id="agent-tab-edit">
            <div class="form-group">
                <label>Agent Name</label>
                <input id="editAgentName" value="${escapeHtml(a.name)}" placeholder="Agent name">
            </div>
            <div class="grid" style="grid-template-columns:1fr 1fr;">
                <div class="form-group">
                    <label>AI Model</label>
                    <select id="editAgentModel">
                        <optgroup label="✨ Google Gemini (Best for Multilingual)">
                            <option value="gemini-2.5-flash" ${a.model === 'gemini-2.5-flash' || !a.model ? 'selected' : ''}>Gemini 2.5 Flash — Best for Danish & Speed</option>
                            <option value="gemini-2.0-flash" ${a.model === 'gemini-2.0-flash' ? 'selected' : ''}>Gemini 2.0 Flash — Fast & Reliable</option>
                            <option value="gemini-1.5-pro" ${a.model === 'gemini-1.5-pro' ? 'selected' : ''}>Gemini 1.5 Pro — High Accuracy, Slower</option>
                        </optgroup>
                        <optgroup label="⚡ Groq (Fastest)">
                            <option value="llama-3.3-70b-versatile" ${a.model === 'llama-3.3-70b-versatile' ? 'selected' : ''}>⚡ Llama 3.3 70B (Groq) — ~150ms</option>
                            <option value="llama-3.1-8b-instant" ${a.model === 'llama-3.1-8b-instant' ? 'selected' : ''}>⚡ Llama 3.1 8B Instant (Groq) — ~80ms</option>
                        </optgroup>
                        <optgroup label="🤖 OpenAI">
                            <option value="gpt-4o-mini" ${a.model === 'gpt-4o-mini' ? 'selected' : ''}>GPT-4o Mini — ~600ms</option>
                            <option value="gpt-4o" ${a.model === 'gpt-4o' ? 'selected' : ''}>GPT-4o — ~900ms</option>
                        </optgroup>
                    </select>
                </div>
                <div class="form-group">
                    <label>Voice</label>
                    <div style="display:flex;gap:.4rem;">
                        <select id="editAgentVoice" style="flex:1;">${voiceSelectHtml}</select>
                        <button type="button" class="btn btn-secondary btn-small" onclick="previewEditVoice()">▶️</button>
                    </div>
                </div>
                <div class="form-group">
                    <label>Language</label>
                    <select id="editAgentLanguage">
                        <option value="en" ${a.language === 'en' ? 'selected' : ''}>🇺🇸 English Only</option>
                        <option value="da" ${a.language === 'da' ? 'selected' : ''}>🇩🇰 Danish Only</option>
                    </select>
                </div>
            </div>

            <div class="form-group">
                <label>System Prompt</label>
                <textarea id="editAgentPrompt" rows="7">${escapeHtml(a.system_prompt || '')}</textarea>
            </div>
            <div style="display:flex;gap:.75rem;justify-content:flex-end;flex-wrap:wrap;">
                <button class="btn btn-secondary" onclick="closeAgentModal()">Cancel</button>
                <button class="btn btn-primary" onclick="saveAgentDetail('${id}')">💾 Save Changes</button>
            </div>
        </div>

        <!-- KB TAB -->
        <div id="agent-tab-kb" style="display:none;">
            <div style="margin-bottom:1rem;">
                <p style="font-size:.85rem;color:var(--text-muted);margin-bottom:.75rem;">Add or remove knowledge base files for this agent.</p>
                <div id="kb-file-list-${id}">
                    ${fileRows || '<p style="color:var(--text-muted);font-size:.85rem;">No files uploaded yet.</p>'}
                </div>
            </div>
            <hr>
            <div class="form-group" style="margin-top:1rem;">
                <label>Upload New Files</label>
                <input type="file" id="newKbFilesModal" multiple accept=".pdf,.txt,.csv" onchange="handleModalFileSelection(event)" style="padding:.5rem;background:transparent;border:1px dashed var(--border);">
                <div id="modalFilePreview" style="margin-top:.5rem;display:flex;flex-wrap:wrap;gap:.25rem;"></div>
            </div>
            <div style="display:flex;gap:.75rem;justify-content:flex-end;flex-wrap:wrap;">
                <button class="btn btn-secondary" onclick="closeAgentModal()">Close</button>
                <button class="btn btn-primary" onclick="uploadKbFilesModal('${id}')">⬆️ Upload Files</button>
            </div>
        </div>`;

    } catch (e) {
        body.innerHTML = '<div style="color:var(--danger);padding:1rem;">Error loading agent details.</div>';
    }
}

function switchAgentTab(tab) {
    const editDiv = document.getElementById('agent-tab-edit');
    const kbDiv = document.getElementById('agent-tab-kb');
    const editBtn = document.getElementById('tab-edit-btn');
    const kbBtn = document.getElementById('tab-kb-btn');
    if (!editDiv || !kbDiv) return;
    if (tab === 'edit') {
        editDiv.style.display = ''; kbDiv.style.display = 'none';
        editBtn.style.color = 'var(--primary)'; editBtn.style.borderBottom = '2px solid var(--primary)'; editBtn.style.marginBottom = '-2px';
        kbBtn.style.color = 'var(--text-muted)'; kbBtn.style.borderBottom = 'none'; kbBtn.style.marginBottom = '0';
    } else {
        editDiv.style.display = 'none'; kbDiv.style.display = '';
        kbBtn.style.color = 'var(--primary)'; kbBtn.style.borderBottom = '2px solid var(--primary)'; kbBtn.style.marginBottom = '-2px';
        editBtn.style.color = 'var(--text-muted)'; editBtn.style.borderBottom = 'none'; editBtn.style.marginBottom = '0';
    }
}

async function deleteKbFileInModal(assistantId, fileId) {
    const result = await Swal.fire({ title: 'Remove file?', text: 'This will delete it from Vapi permanently.', icon: 'warning', showCancelButton: true, confirmButtonColor: '#ef4444' });
    if (result.isConfirmed) {
        try {
            await apiCall(`/api/assistant/${assistantId}/kb-files/${fileId}`, { method: 'DELETE' });
            // Reload modal to refresh file list
            openAgentDetail(assistantId);
            setTimeout(() => switchAgentTab('kb'), 100); // stay on KB tab
        } catch (e) { Swal.fire('Error', 'Error deleting file', 'error'); }
    }
}

async function uploadKbFilesModal(assistantId) {
    if (!pendingModalFiles.length) { Swal.fire('No files', 'Please select files to upload.', 'info'); return; }
    Swal.fire({ title: 'Uploading...', allowOutsideClick: false, didOpen: () => Swal.showLoading() });
    const fd = new FormData();
    for (let i = 0; i < pendingModalFiles.length; i++) fd.append('files', pendingModalFiles[i]);
    try {
        const res = await apiCall(`/api/assistant/${assistantId}/add-files`, { method: 'POST', body: fd });
        if (res.ok) {
            Swal.fire({ icon: 'success', title: 'Uploaded!', timer: 1200, showConfirmButton: false });
            pendingModalFiles = []; // Clear pending list
            openAgentDetail(assistantId);
            setTimeout(() => switchAgentTab('kb'), 100);
        } else { Swal.fire('Error', 'Upload failed.', 'error'); }
    } catch (e) { Swal.fire('Error', 'Network error.', 'error'); }
}

async function buildVoiceSelectHtml(selectedId) {
    try {
        const [vapiRes, elRes] = await Promise.allSettled([
            apiCall("/api/vapi-voices"),
            fetch("https://api.elevenlabs.io/v1/voices").then(r => r.json())
        ]);
        
        const vapiVoices = vapiRes.status === 'fulfilled' ? await vapiRes.value.json() : [];
        const elVoices = elRes.status === 'fulfilled' ? elRes.value.voices : [];
        
        const mergedVoices = [...vapiVoices];
        const existingIds = new Set(vapiVoices.map(v => v.id || v.voiceId));
        
        if (elVoices) {
            elVoices.forEach(ev => {
                if (!existingIds.has(ev.voice_id)) {
                    mergedVoices.push({
                        id: ev.voice_id,
                        name: ev.name,
                        provider: '11labs',
                        previewUrl: ev.preview_url
                    });
                }
            });
        }
        
        const elPreviews = {};
        if (elVoices) elVoices.forEach(v => { elPreviews[v.voice_id] = v.preview_url; });
        
        const targetVoiceId = 'Hp07ONf6C5qlCKOeB4oo';
        let html = '';
        
        mergedVoices.forEach(v => {
            const vid = v.id || v.voiceId;
            const preview = v.previewUrl || v.preview_url || elPreviews[vid] || '';
            const isSelected = vid === selectedId || (!selectedId && (vid === targetVoiceId || (v.name && v.name.toLowerCase().includes('constantin'))));
            html += `<option value="${vid}" data-preview="${preview}" ${isSelected ? 'selected' : ''}>${isSelected ? '⭐ ' : ''}${v.name} (${v.provider || 'vapi'})</option>`;
        });
        return html || `<option value="${selectedId}" selected>Current Voice</option>`;
    } catch (e) {
        return `<option value="${selectedId}" selected>Current Voice</option>`;
    }
}


function previewEditVoice() {
    const sel = document.getElementById('editAgentVoice');
    const opt = sel.options[sel.selectedIndex];
    if (!opt || opt.disabled) return;
    const url = opt.dataset ? opt.dataset.preview : opt.getAttribute('data-preview');
    if (!url) { Swal.fire('No Preview', 'No preview available.', 'info'); return; }
    if (currentAudio) { currentAudio.pause(); currentAudio.currentTime = 0; }
    currentAudio = new Audio(url);
    currentAudio.play();
}

async function saveAgentDetail(id) {
    const name = document.getElementById('editAgentName').value.trim();
    const prompt = document.getElementById('editAgentPrompt').value.trim();
    const model = document.getElementById('editAgentModel').value;
    const voiceSel = document.getElementById('editAgentVoice');
    const voice_id = voiceSel ? voiceSel.value : null;
    const language = document.getElementById('editAgentLanguage').value;

    if (!name) { Swal.fire('Validation', 'Agent name cannot be empty.', 'warning'); return; }

    const payload = { name, system_prompt: prompt, model, language };
    if (voice_id) payload.voice_id = voice_id;

    try {
        const res = await apiCall(`/api/assistant/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            Swal.fire({ icon: 'success', title: 'Saved!', text: 'Agent updated successfully.', timer: 1500, showConfirmButton: false });
            closeAgentModal();
            loadAssistants();
        } else {
            const err = await res.json();
            Swal.fire('Error', err.detail || 'Failed to update agent.', 'error');
        }
    } catch (e) { Swal.fire('Error', 'Network error.', 'error'); }
}

function closeAgentModal() {
    const modal = document.getElementById('agentDetailModal');
    if (modal) modal.classList.remove('active');
    currentEditId = null;
    pendingModalFiles = []; // Reset pending list on close
}

function escapeHtml(str) {
    return (str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

// DELETE ASSISTANT (also deletes KB files from Vapi via backend)
async function deleteAssistant(id) {
    const result = await Swal.fire({
        title: 'Delete Agent?',
        text: 'This will delete the agent AND all its knowledge base files from Vapi. Cannot be undone.',
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#ef4444',
        cancelButtonColor: '#64748b',
        confirmButtonText: 'Yes, delete everything!'
    });
    if (result.isConfirmed) {
        try {
            Swal.fire({ title: 'Deleting...', allowOutsideClick: false, didOpen: () => Swal.showLoading() });
            await apiCall(`/api/assistant/${id}`, { method: 'DELETE' });
            Swal.fire('Deleted!', 'Agent and all its files have been removed.', 'success');
            loadAssistants();
        } catch (e) {
            Swal.fire('Error!', 'Error deleting agent.', 'error');
        }
    }
}

// CALL
async function startCall(id) {
    const { value: phone } = await Swal.fire({
        title: 'Test Voice Call',
        input: 'text',
        inputLabel: 'Enter phone number with country code (e.g., +8801xxxx):',
        inputPlaceholder: '+8801...',
        showCancelButton: true
    });
    if (phone) {
        try {
            await apiCall('/api/start-call', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ assistant_id: id, phone_number: phone }) });
            Swal.fire('Success', 'Call initiated! Check the Call Logs tab for status.', 'success');
            loadCalls();
        } catch (e) { Swal.fire('Error', 'Error initiating call.', 'error'); }
    }
}

// CALLS
async function loadCalls() {
    let filter = document.getElementById('filterAssistant').value;
    let url = filter ? `/api/calls?assistant_id=${filter}` : '/api/calls';
    let table = document.getElementById('callsTable');
    if (!table) return;
    table.innerHTML = '<tr><td style="text-align:center;padding:2rem;">Loading calls...</td></tr>';
    try {
        let res = await apiCall(url);
        let data = await res.json();
        if (!data.calls || !data.calls.length) {
            table.innerHTML = '<tr><td style="text-align:center;padding:2rem;color:var(--text-muted);">No calls found.</td></tr>';
            return;
        }
        table.innerHTML = `<tr><th>Type</th><th>Agent</th><th>Phone</th><th>Started At</th><th>Status</th><th>Duration</th><th>Cost</th><th>Audio & Transcripts</th></tr>` +
            data.calls.map(c => {
                let agent = assistantsList.find(a => a.id === c.assistant_id) || { name: 'Unknown' };
                let sc = c.status === 'completed' ? '#10b981' : c.status === 'failed' ? '#ef4444' : c.status === 'in-progress' ? '#3b82f6' : '#64748b';
                let typeColor = c.type.includes('Inbound') ? '#3b82f6' : '#10b981';
                let costStr = c.cost ? `$${parseFloat(c.cost).toFixed(4)}` : '$0.0000';
                
                return `<tr>
                <td><span style="font-size:.7rem;font-weight:700;padding:.2rem .5rem;border-radius:20px;background:${typeColor}20;color:${typeColor}; text-transform:uppercase;">${c.type}</span></td>
                <td style="font-weight:600;">${agent.name}</td>
                <td style="font-family:monospace;">${c.phone_number}</td>
                <td style="color:var(--text-muted);font-size:.85rem;">${new Date(c.started_at).toLocaleString()}</td>
                <td><span class="status-badge" style="background:${sc}20;color:${sc};">${c.status}</span></td>
                <td>${c.duration}s</td>
                <td style="font-family:monospace; font-weight:600; font-size:0.8rem; color:var(--text-muted);">${costStr}</td>
                <td>
                    <div style="display:flex;align-items:center;gap:.75rem;min-width:300px;">
                    ${c.recording_url ? `
                        <div style="background:var(--bg-color);padding:.5rem;border-radius:var(--radius-md);border:1px solid var(--border);flex:1;display:flex;align-items:center;">
                            <audio controls style="height:28px; width:100%;">
                                <source src="${c.recording_url}" type="audio/mpeg">
                            </audio>
                        </div>
                    ` : '<span style="color:var(--text-muted);font-size:.8rem;">No recording</span>'}
                    ${c.transcript ? `<button class="transcript-btn" onclick="viewTranscript('${btoa(unescape(encodeURIComponent(c.transcript)))}')" style="white-space:nowrap;padding:.5rem .75rem;">📜 Transcript</button>` : ''}
                    </div>
                </td>
            </tr>`;
            }).join('');
    } catch (e) {
        table.innerHTML = '<tr><td style="text-align:center;padding:2rem;color:var(--danger);">Error loading call logs.</td></tr>';
    }
}

function viewTranscript(base64) {
    const raw = decodeURIComponent(escape(atob(base64)));
    
    // Parse the transcript into bubbles
    // Format is usually "User: message" or "Assistant: message"
    const lines = raw.split('\n').filter(l => l.trim().length > 0);
    let html = '<div class="transcript-box">';
    
    lines.forEach(line => {
        let speaker = "Unknown";
        let text = line;
        
        if (line.toLowerCase().startsWith('user:')) {
            speaker = "User";
            text = line.substring(5).trim();
        } else if (line.toLowerCase().startsWith('assistant:')) {
            speaker = "AI";
            text = line.substring(10).trim();
        } else if (line.toLowerCase().startsWith('ai:')) {
            speaker = "AI";
            text = line.substring(3).trim();
        } else {
            // Try to find any colon as a separator
            const colonIdx = line.indexOf(':');
            if (colonIdx > 0 && colonIdx < 15) {
                const possibleSpeaker = line.substring(0, colonIdx).trim().toLowerCase();
                if (possibleSpeaker.includes('user')) { speaker = "User"; text = line.substring(colonIdx + 1).trim(); }
                else if (possibleSpeaker.includes('assistant') || possibleSpeaker.includes('ai') || possibleSpeaker.includes('bot')) { speaker = "AI"; text = line.substring(colonIdx + 1).trim(); }
            }
        }

        const isUser = speaker === "User";
        html += `
        <div class="trn-row ${isUser ? 'trn-row-user' : 'trn-row-ai'}">
            <div class="trn-msg ${isUser ? 'trn-user' : 'trn-ai'}">
                ${text}
            </div>
        </div>`;
    });
    
    html += '</div>';

    Swal.fire({
        title: 'Conversation Transcript',
        html: html,
        width: '650px',
        confirmButtonText: 'Close',
        customClass: {
            container: 'transcript-modal-container'
        }
    });
}

function updateFilterSelect() {
    const sel = document.getElementById('filterAssistant');
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = '<option value="">All Agents</option>' + assistantsList.map(a => `<option value="${a.id}">${a.name}</option>`).join('');
    sel.value = cur;
}

// ORDERS
async function loadOrders() {
    let table = document.getElementById('ordersTable');
    if (!table) return;
    table.innerHTML = '<tr><td style="text-align:center;padding:2rem;">Loading orders...</td></tr>';
    try {
        let res = await apiCall('/api/orders');
        let data = await res.json();
        
        if (!data.orders || data.orders.length === 0) {
            table.innerHTML = '<tr><td style="text-align:center;padding:2rem;color:var(--text-muted);">No orders found in the database.</td></tr>';
            return;
        }

        let rows = data.orders.map(o => {
            let itemsHtml = '<div style="display:flex; flex-direction:column; gap:4px;">No items</div>';
            try {
                const items = JSON.parse(o.order || "[]");
                if (Array.isArray(items) && items.length > 0) {
                    itemsHtml = '<div style="display:flex; flex-direction:column; gap:4px;">' + 
                        items.map(i => `
                            <div style="background:var(--bg-color); border:1px solid var(--border); padding:2px 8px; border-radius:12px; font-size:0.7rem; display:inline-block; white-space:nowrap;">
                                <span style="font-weight:700; color:var(--primary);">${i.quantity || 1}x</span> ${i.name || 'Item'} <span style="color:var(--text-muted); font-size:0.65rem;">(${i.size || 'N/A'})</span>
                            </div>
                        `).join("") + '</div>';
                }
            } catch(e) {}
            
            const source = (o.call_id || "").startsWith("chat_") ? "💬 Chat" : "📞 Call";
            const sColor = source.includes("Chat") ? "#3b82f6" : "#10b981";
            const dateStr = o.created_at ? new Date(o.created_at).toLocaleString() : "Unknown";

            return `<tr>
                <td style="color:var(--text-muted);font-size:.75rem;">#${o.id}</td>
                <td style="font-weight:600;">${o.name}</td>
                <td>${o.phone}</td>
                <td style="font-size:.85rem;line-height:1.4;">${itemsHtml}</td>
                <td style="font-weight:700;color:var(--primary);">$${Number(o.total).toFixed(2)}</td>
                <td><span style="font-size:.7rem;font-weight:700;padding:.2rem .5rem;border-radius:20px;background:${sColor}20;color:${sColor};text-transform:uppercase;">${source}</span></td>
                <td style="color:var(--text-muted);font-size:.8rem;">${dateStr}</td>
                <td><button class="btn btn-small btn-danger" onclick="deleteOrder(${o.id})">🗑️ Delete</button></td>
            </tr>`;
        }).join("");

        table.innerHTML = `<tr><th>ID</th><th>Customer</th><th>Phone</th><th>Items</th><th>Total</th><th>Source</th><th>Date</th><th>Actions</th></tr>` + rows;
    } catch (e) {
        console.error("Orders Error:", e);
        table.innerHTML = '<tr><td style="text-align:center;padding:2rem;color:var(--danger);">Failed to load orders. Please refresh.</td></tr>';
    }
}

async function deleteOrder(id) {
    const result = await Swal.fire({
        title: 'Delete Order?',
        text: "This removal is permanent.",
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#ef4444'
    });
    if (result.isConfirmed) {
        try {
            const res = await apiCall(`/api/orders/${id}`, { method: 'DELETE' });
            if (res.ok) {
                Swal.fire({ icon: 'success', title: 'Deleted!', timer: 1000, showConfirmButton: false });
                loadOrders();
            }
        } catch (e) { Swal.fire('Error', 'Failed to delete order.', 'error'); }
    }
}

// CHAT
async function sendChat() {
    let assistantId = document.getElementById('chatAssistantSelect').value;
    let input = document.getElementById('chatInput');
    let msg = input.value.trim();
    if (!assistantId || !msg) return;
    let chatDiv = document.getElementById('chatMessages');
    if (!chatDiv) return;
    if (chatDiv.innerHTML.includes('Select an agent')) chatDiv.innerHTML = '';
    chatDiv.innerHTML += `<div class="msg-bubble msg-user">${msg}</div>`;
    
    input.value = ''; input.disabled = true;
    chatDiv.scrollTop = chatDiv.scrollHeight;
    
    console.log(`[Chat] Sending message via session: ${chatSessionId}`);

    try {
        let res = await apiCall('/api/chat-with-agent', { 
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' }, 
            body: JSON.stringify({ 
                assistant_id: assistantId, 
                message: msg,
                session_id: chatSessionId 
            }) 
        });
        let data = await res.json();
        if (data.response) {
            chatDiv.innerHTML += `<div class="msg-bubble msg-agent" style="line-height:1.6;">${marked.parse(data.response)}</div>`;
        } else {
            chatDiv.innerHTML += `<div class="msg-bubble msg-agent" style="color:var(--danger)">No response received.</div>`;
        }
    } catch (e) {
        console.error("[Chat] Error:", e);
        chatDiv.innerHTML += `<div class="msg-bubble msg-agent" style="color:var(--danger)">Network Error during chat.</div>`;
    } finally {
        input.disabled = false; input.focus();
        chatDiv.scrollTop = chatDiv.scrollHeight;
    }
}

function loadChatSelect() {
    let sel = document.getElementById('chatAssistantSelect');
    if (sel) {
        let current = sel.value;
        sel.innerHTML = '<option value="">-- Choose an Agent --</option>' + assistantsList.map(a => `<option value="${a.id}">${a.name}</option>`).join('');
        if (current) sel.value = current;
        
        // Remove the automatic currentChatHistory = []; reset to allow persistence
        sel.onchange = () => {
            let chatDiv = document.getElementById('chatMessages');
            if (chatDiv) {
                // We keep the internal chatHistories[id] but we clear the screen for clarity
                chatDiv.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--text-muted);font-size:.9rem;">Switched agent. History is preserved internally...</div>';
            }
        };
    }
}

// KNOWLEDGE BASE MODAL
async function manageKB(assistantId) {
    try {
        const res = await apiCall(`/api/assistant/${assistantId}/kb-files`);
        const data = await res.json();
        let fileListHtml = data.files.map(f => `
        <li style="display:flex;justify-content:space-between;align-items:center;">
            <span>📄 ${escapeHtml(f.name)}</span>
            <button class="btn btn-small btn-danger" onclick="deleteKbFile('${assistantId}','${f.vapi_file_id}')">🗑️ Remove</button>
        </li>`).join('');
        document.body.insertAdjacentHTML('beforeend', `
        <div id="kbModal" class="modal">
            <div class="modal-content">
                <div class="modal-header">
                    <h3>📂 Knowledge Base Files</h3>
                    <button class="close-btn" onclick="closeModal()">×</button>
                </div>
                <p style="color:var(--text-muted);margin-bottom:1rem;font-size:.875rem;">Files uploaded here act as context for the AI. Removing them also removes them from Vapi.</p>
                <ul class="kb-list">${fileListHtml || '<li style="justify-content:center;color:var(--text-muted);">No documents uploaded yet.</li>'}</ul>
                <div style="background:var(--bg-color);padding:1rem;border-radius:var(--radius-md);border:1px solid var(--border);">
                    <label style="margin-bottom:.5rem;display:block;">Add More Documents</label>
                    <input type="file" id="newKbFiles" multiple accept=".pdf,.txt,.csv" style="margin-bottom:1rem;">
                    <button class="btn btn-primary" onclick="uploadMoreFiles('${assistantId}')" style="width:100%;">Upload Files</button>
                </div>
            </div>
        </div>`);
        setTimeout(() => document.getElementById('kbModal').classList.add('active'), 10);
    } catch (e) { Swal.fire('Oops...', 'Error loading Knowledge Base files.', 'error'); }
}

async function deleteKbFile(assistantId, fileId) {
    const result = await Swal.fire({ title: 'Remove file?', text: 'This will remove the file from the knowledge base AND delete it from Vapi.', icon: 'warning', showCancelButton: true, confirmButtonColor: '#ef4444' });
    if (result.isConfirmed) {
        try {
            await apiCall(`/api/assistant/${assistantId}/kb-files/${fileId}`, { method: 'DELETE' });
            closeModal();
            setTimeout(() => manageKB(assistantId), 150);
        } catch (e) { Swal.fire('Error', 'Error deleting file', 'error'); }
    }
}

async function uploadMoreFiles(assistantId) {
    let files = document.getElementById('newKbFiles').files;
    if (!files.length) return;
    const btn = document.querySelector('#kbModal .btn-primary');
    if (!btn) return;
    const origText = btn.innerHTML; btn.innerHTML = 'Uploading...'; btn.disabled = true;
    let fd = new FormData();
    for (let i = 0; i < files.length; i++) fd.append('files', files[i]);
    try {
        const res = await apiCall(`/api/assistant/${assistantId}/add-files`, { method: 'POST', body: fd });
        if (res.ok) { closeModal(); loadAssistants(); }
        else { Swal.fire('Error', 'Error adding files.', 'error'); }
    } catch (e) { Swal.fire('Network Error', 'Network error while uploading.', 'error'); }
    finally { btn.innerHTML = origText; btn.disabled = false; }
}

function closeModal() {
    let modal = document.getElementById('kbModal');
    if (modal) { modal.classList.remove('active'); setTimeout(() => modal.remove(), 200); }
}

// TELEPHONY - Twilio + Vonage
async function loadTelephonyNumbers() {
    let table = document.getElementById('telephonyTable');
    if (!table) return;
    try {
        let res = await apiCall('/api/telephony/numbers');
        let data = await res.json();
        if (!data || !data.length) {
            table.innerHTML = '<tr><td style="text-align:center;padding:2rem;color:var(--text-muted);">No phone numbers linked yet. Click "Import Number" to add one.</td></tr>';
            return;
        }
        table.innerHTML = `<tr><th>Phone Number</th><th>Provider</th><th>Linked Agent</th><th>Actions</th></tr>` +
            data.map(n => {
                let agent = assistantsList.find(a => a.id === n.assistantId) || { name: 'Unlinked' };
                const provColor = n.provider === 'vonage' ? '#e11d48' : '#dc2626';
                return `<tr>
                <td style="font-weight:700;font-size:1rem;letter-spacing:.5px;">${n.number}</td>
                <td><span class="status-badge" style="background:${provColor}20;color:${provColor};">${(n.provider || 'twilio').toUpperCase()}</span></td>
                <td>${agent.name}</td>
                <td><button class="btn btn-danger btn-small" onclick="deleteTelephonyNumber('${n.id}')">Unlink 🗑️</button></td>
            </tr>`;
            }).join('');
    } catch (e) {
        table.innerHTML = '<tr><td style="text-align:center;padding:2rem;color:var(--danger);">Error loading numbers. Start backend server.</td></tr>';
    }
}

async function openImportNumberModal() {
    let optionsHtml = '<option value="">-- No Agent (Unlinked) --</option>';
    assistantsList.forEach(a => { optionsHtml += `<option value="${a.id}">${a.name}</option>`; });

    const { value: formValues } = await Swal.fire({
        title: '📞 Import VoIP Number',
        width: 520,
        html: `
        <div style="text-align:left;">
            <div class="form-group">
                <label style="font-size:.875rem;font-weight:600;">Provider</label>
                <select id="swal-provider" class="swal2-input" style="width:100%;max-width:100%;margin:.5rem 0 1rem;" onchange="handleProviderChange()">
                    <option value="twilio">Twilio</option>
                    <option value="vonage">Vonage</option>
                </select>
            </div>
            <div class="form-group">
                <label style="font-size:.875rem;font-weight:600;">Phone Number</label>
                <input id="swal-number" class="swal2-input" style="width:100%;max-width:100%;margin:.5rem 0 1rem;" placeholder="+1234567890">
            </div>
            <div class="form-group">
                <label id="swal-sid-label" style="font-size:.875rem;font-weight:600;">Account SID</label>
                <input id="swal-sid" class="swal2-input" style="width:100%;max-width:100%;margin:.5rem 0 1rem;" placeholder="Account SID / API Key">
            </div>
            <div class="form-group">
                <label id="swal-token-label" style="font-size:.875rem;font-weight:600;">Auth Token</label>
                <input id="swal-token" type="password" class="swal2-input" style="width:100%;max-width:100%;margin:.5rem 0 1rem;" placeholder="Auth Token / API Secret">
            </div>
            <div class="form-group">
                <label style="font-size:.875rem;font-weight:600;">Link to Agent</label>
                <select id="swal-assistant" class="swal2-input" style="width:100%;max-width:100%;padding:0 1rem;margin:.5rem 0;">${optionsHtml}</select>
            </div>
        </div>`,
        focusConfirm: false,
        showCancelButton: true,
        confirmButtonText: 'Import 📞',
        didOpen: () => { handleProviderChange(); },
        preConfirm: () => {
            const provider = document.getElementById('swal-provider').value;
            const number = document.getElementById('swal-number').value.trim();
            const sid = document.getElementById('swal-sid').value.trim();
            const token_val = document.getElementById('swal-token').value.trim();
            const assistantId = document.getElementById('swal-assistant').value;
            if (!number || !sid || !token_val) {
                Swal.showValidationMessage('Number, Account credentials are required');
                return false;
            }
            if (provider === 'vonage') {
                return { provider, number, vonageApiKey: sid, vonageApiSecret: token_val, assistantId };
            } else {
                return { provider, number, twilioAccountSid: sid, twilioAuthToken: token_val, assistantId };
            }
        }
    });

    if (formValues) {
        try {
            Swal.fire({ title: 'Importing...', allowOutsideClick: false, didOpen: () => Swal.showLoading() });
            const res = await apiCall('/api/telephony/numbers', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(formValues) });
            if (res.ok) {
                Swal.fire('Success', 'Phone number imported successfully!', 'success');
                loadTelephonyNumbers();
            } else {
                let err = await res.json();
                Swal.fire('Error', err.detail || 'Failed to import. Check credentials.', 'error');
            }
        } catch (e) { Swal.fire('Error', 'Network error during import.', 'error'); }
    }
}

function handleProviderChange() {
    const provider = document.getElementById('swal-provider')?.value;
    const sidLabel = document.getElementById('swal-sid-label');
    const tokenLabel = document.getElementById('swal-token-label');
    const sidInput = document.getElementById('swal-sid');
    const tokenInput = document.getElementById('swal-token');
    if (!sidLabel) return;
    if (provider === 'vonage') {
        sidLabel.textContent = 'Vonage API Key';
        tokenLabel.textContent = 'Vonage API Secret';
        sidInput.placeholder = 'Your Vonage API Key';
        tokenInput.placeholder = 'Your Vonage API Secret';
    } else {
        sidLabel.textContent = 'Twilio Account SID';
        tokenLabel.textContent = 'Twilio Auth Token';
        sidInput.placeholder = 'ACxxxxxxxxxxx';
        tokenInput.placeholder = 'Auth Token';
    }
}

async function deleteTelephonyNumber(id) {
    let result = await Swal.fire({
        title: 'Unlink Number?',
        text: "This will remove the number from Vapi configuration.",
        icon: 'warning', showCancelButton: true, confirmButtonColor: '#ef4444', confirmButtonText: 'Yes, Unlink'
    });
    if (result.isConfirmed) {
        try {
            const res = await apiCall(`/api/telephony/numbers/${id}`, { method: 'DELETE' });
            if (res.ok) { Swal.fire('Unlinked!', 'The number has been removed.', 'success'); loadTelephonyNumbers(); }
            else { Swal.fire('Error', 'Could not remove number.', 'error'); }
        } catch (e) { Swal.fire('Error', 'Error executing request.', 'error'); }
    }
}

// BILLING — Vapi has no balance API; show dashboard link
function loadBillingInfo() {
    // Nothing to load — the card already has the static link
}

// VOICE CALL (Vapi Web SDK)
let vapiClient = null, vapiActive = false;

async function setupVapi(pubKey) {
    try {
        const VapiModule = await import('https://esm.sh/@vapi-ai/web');
        const Vapi = VapiModule.default || VapiModule.Vapi || VapiModule;
        vapiClient = new Vapi(pubKey);
        vapiClient.on('call-start', () => {
            vapiActive = true;
            const btn = document.getElementById('toggleVoiceBtn');
            if (btn) {
                btn.innerHTML = '🛑 End Call'; btn.style.background = 'var(--danger)';
            }
            let chatDiv = document.getElementById('chatMessages');
            if (chatDiv) {
                if (chatDiv.innerHTML.includes('Select an agent')) chatDiv.innerHTML = '';
                chatDiv.innerHTML += `<div style="text-align:center;padding:10px;color:var(--success);font-weight:bold;">🎙️ Voice call connected!</div>`;
                chatDiv.scrollTop = chatDiv.scrollHeight;
            }
        });
        vapiClient.on('call-end', () => {
            vapiActive = false;
            const btn = document.getElementById('toggleVoiceBtn');
            if (btn) {
                btn.innerHTML = '🎙️ Voice'; btn.style.background = 'var(--success)';
            }
            let chatDiv = document.getElementById('chatMessages');
            if (chatDiv) {
                chatDiv.innerHTML += `<div style="text-align:center;padding:10px;color:var(--danger);font-weight:bold;">Voice call disconnected.</div>`;
                chatDiv.scrollTop = chatDiv.scrollHeight;
            }
        });
        vapiClient.on('message', (msg) => {
            if (msg.type === 'transcript' && msg.transcriptType === 'final') {
                let chatDiv = document.getElementById('chatMessages');
                if (chatDiv) {
                    const roleClass = msg.role === 'user' ? 'msg-user' : 'msg-agent';
                    chatDiv.innerHTML += `<div class="msg-bubble ${roleClass}">${msg.transcript}</div>`;
                    chatDiv.scrollTop = chatDiv.scrollHeight;
                }
            }
        });
        vapiClient.on('error', (err) => {
            console.error('Vapi Error:', err); vapiActive = false;
            const btn = document.getElementById('toggleVoiceBtn');
            if (btn) {
                btn.innerHTML = '🎙️ Voice'; btn.style.background = 'var(--success)';
            }
        });
    } catch (e) { console.error('Could not initialize Vapi client', e); }
}

async function toggleVoiceCall() {
    let assistantId = document.getElementById('chatAssistantSelect').value;
    if (!assistantId) { Swal.fire('Info', 'Please select an agent first!', 'info'); return; }

    let pubKey = localStorage.getItem('vapi_public_key');
    // If key is missing OR is a placeholder, try fetching from backend
    if (!pubKey || pubKey.includes('your-vapi')) {
        try {
            // Using direct fetch instead of apiCall to avoid auth dependency for public config
            const cfgRes = await fetch('/api/config');
            if (cfgRes.ok) {
                const cfg = await cfgRes.json();
                if (cfg.vapi_public_key && !cfg.vapi_public_key.includes('your-vapi')) {
                    pubKey = cfg.vapi_public_key;
                    localStorage.setItem('vapi_public_key', pubKey);
                }
            }
        } catch (e) {
            console.error('Failed to fetch Vapi config from backend', e);
        }
    }

    if (!pubKey || pubKey.includes('your-vapi')) {
        console.error('Vapi Public Key is missing or invalid in .env');
        Swal.fire('Configuration Error', 'Vapi Public Key is not configured correctly in the backend. Please check your .env file.', 'error');
        return;
    }

    if (!vapiClient) { await setupVapi(pubKey); }
    if (!vapiClient) { Swal.fire('Error', 'Voice SDK failed to load. Check console.', 'error'); return; }
    if (vapiActive) { vapiClient.stop(); }
    else {
        const btn = document.getElementById('toggleVoiceBtn');
        if (btn) {
            btn.innerHTML = '⏳ Connecting...'; btn.style.background = '#f59e0b';
        }
        vapiClient.start(assistantId);
    }
}

// NAV / THEME / TABS
function switchTab(tabId) {
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    const targetTab = document.getElementById(tabId + 'Tab');
    if (targetTab) targetTab.classList.add('active');
    
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    const navItem = document.querySelector(`.nav-item[data-tab="${tabId}"]`);
    if (navItem) navItem.classList.add('active');
    
    closeSidebar();
    if (tabId === 'manage') loadAssistants();
    if (tabId === 'orders') loadOrders();
    if (tabId === 'calls') loadCalls();
    if (tabId === 'telephony') loadTelephonyNumbers();
    if (tabId === 'settings') loadBillingInfo();
}

function toggleTheme() {
    isDarkTheme = !isDarkTheme;
    if (isDarkTheme) { document.documentElement.setAttribute('data-theme', 'dark'); localStorage.setItem('theme', 'dark'); }
    else { document.documentElement.removeAttribute('data-theme'); localStorage.setItem('theme', 'light'); }
}

document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => switchTab(item.dataset.tab));
});

// INIT
async function initDashboard() {
    fetchElevenLabsVoices();
    loadAssistants();
    loadOrders();
    loadCalls();
    loadTelephonyNumbers();

    try {
        const cfgRes = await fetch('/api/config');
        if (cfgRes.ok) {
            const cfg = await cfgRes.json();
            if (cfg.vapi_public_key && !cfg.vapi_public_key.includes('your-vapi')) {
                localStorage.setItem('vapi_public_key', cfg.vapi_public_key);
                setupVapi(cfg.vapi_public_key);
            }
        }
    } catch (e) { console.warn('Could not pre-load Vapi key', e); }
}

// Initial state check
window.addEventListener('DOMContentLoaded', () => {
    if (token) {
        const loginPage = document.getElementById('loginPage');
        const appContainer = document.getElementById('appContainer');
        if (loginPage) loginPage.style.display = 'none';
        if (appContainer) appContainer.style.display = 'block';
        initDashboard();
    } else {
        const loginPage = document.getElementById('loginPage');
        const appContainer = document.getElementById('appContainer');
        if (loginPage) loginPage.style.display = 'flex';
        if (appContainer) appContainer.style.display = 'none';
    }
});
