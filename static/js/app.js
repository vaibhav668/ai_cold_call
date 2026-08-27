// Platform Frontend State Manager
const API_BASE = '/api/v1';

// Defensive array extraction utility
function getArrayData(data) {
    if (!data) return [];
    if (Array.isArray(data)) return data;
    if (data.items && Array.isArray(data.items)) return data.items;
    if (data.data && Array.isArray(data.data)) return data.data;
    return [];
}

// API Fetch wrapper adding token authentication headers
async function apiFetch(endpoint, options = {}) {
    const token = localStorage.getItem('token');
    const headers = {
        ...(options.headers || {})
    };
    
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }
    
    // Check if body is FormData, if not set JSON header
    if (!(options.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
    }
    
    const response = await fetch(`${API_BASE}${endpoint}`, {
        ...options,
        headers
    });
    
    if (response.status === 401) {
        // Token expired / Unauthorized, show login overlay
        document.getElementById('login-screen').style.display = 'flex';
        localStorage.removeItem('token');
    }
    
    return response;
}

// Check session on page load
window.addEventListener('DOMContentLoaded', () => {
    const token = localStorage.getItem('token');
    if (token) {
        document.getElementById('login-screen').style.display = 'none';
        loadDashboardStats();
    } else {
        document.getElementById('login-screen').style.display = 'flex';
    }
});

// Authentication Sign In
document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = document.getElementById('login-email').value;
    const password = document.getElementById('login-password').value;
    
    const payload = new URLSearchParams();
    payload.append('username', email);
    payload.append('password', password);
    
    try {
        const response = await fetch(`${API_BASE}/auth/login`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            body: payload
        });
        
        if (response.status !== 200) {
            alert('Invalid administrator login credentials.');
            return;
        }
        
        const data = await response.json();
        localStorage.setItem('token', data.access_token);
        document.getElementById('login-screen').style.display = 'none';
        
        // Initial data fetches
        loadDashboardStats();
    } catch (err) {
        console.error(err);
        alert('Failed to connect to authentication endpoints.');
    }
});

// Logout Handoff
function logout() {
    localStorage.removeItem('token');
    document.getElementById('login-screen').style.display = 'flex';
}

// Navigation Tabs Router switches
function switchTab(tabName) {
    // Switch active nav class
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.remove('active');
    });
    event.currentTarget.classList.add('active');
    
    // Switch active tab section
    document.querySelectorAll('.tab-content').forEach(section => {
        section.classList.remove('active');
    });
    document.getElementById(`tab-${tabName}`).classList.add('active');
    
    // Trigger data fetch routines based on active tab view
    if (tabName === 'dashboard') loadDashboardStats();
    else if (tabName === 'campaigns') loadCampaigns();
    else if (tabName === 'customers') loadCustomers();
    else if (tabName === 'knowledge') loadKnowledge();
    else if (tabName === 'logs') loadCallLogs();
}

// Modal actions
function openModal(id) {
    document.getElementById(id).classList.add('open');
}

function closeModal(id) {
    document.getElementById(id).classList.remove('open');
}

// 1. Load Dashboard KPI Metrics
async function loadDashboardStats() {
    try {
        const response = await apiFetch('/analytics/summary');
        if (response.ok) {
            const data = await response.json();
            document.getElementById('stats-active-campaigns').innerText = data.active_campaigns || 0;
            document.getElementById('stats-total-calls').innerText = data.total_calls || 0;
            document.getElementById('stats-success-rate').innerText = `${data.success_rate_percentage || 0}%`;
            document.getElementById('stats-avg-duration').innerText = `${data.average_duration_seconds || 0}s`;
        }
    } catch (err) {
        console.error('Failed to load overview stats: ', err);
    }
}

// 2. Campaigns CRUD list
async function loadCampaigns() {
    try {
        const response = await apiFetch('/campaigns/');
        if (!response.ok) return;
        
        const data = await response.json();
        const tbody = document.getElementById('campaigns-tbody');
        tbody.innerHTML = '';
        
        // Populate knowledge seeder campaign select options as well
        const docSelect = document.getElementById('upload-doc-campaign');
        docSelect.innerHTML = '<option value="">Select Target Campaign...</option>';
        
        const campaigns = getArrayData(data);
        if (campaigns.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">No outbound campaigns created yet.</td></tr>';
            return;
        }
        
        campaigns.forEach(c => {
            // Append target select option
            const opt = document.createElement('option');
            opt.value = c.id;
            opt.innerText = c.name;
            docSelect.appendChild(opt);
            
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td style="font-weight: 600;">${c.name}</td>
                <td style="text-transform: capitalize;">${c.workflow_type}</td>
                <td>Max: ${c.max_retries} | Interval: ${c.retry_interval_minutes}m</td>
                <td><span class="badge badge-active">${c.status}</span></td>
                <td>${new Date(c.created_at).toLocaleDateString()}</td>
                <td>
                    <button class="btn btn-secondary" style="padding: 0.4rem 0.75rem; font-size: 0.8rem;" onclick="triggerDialCampaign('${c.id}')">
                        <i class="fa-solid fa-phone"></i> Run
                    </button>
                    <button class="btn btn-secondary" style="padding: 0.4rem 0.75rem; font-size: 0.8rem; color: var(--danger);" onclick="deleteCampaign('${c.id}')">
                        <i class="fa-solid fa-trash"></i>
                    </button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        console.error(err);
    }
}

// Create Campaign
document.getElementById('campaign-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = document.getElementById('camp-name').value;
    const workflow_type = document.getElementById('camp-workflow').value;
    const max_retries = parseInt(document.getElementById('camp-retries').value);
    const retry_interval_minutes = parseInt(document.getElementById('camp-interval').value);
    
    try {
        const response = await apiFetch('/campaigns/', {
            method: 'POST',
            body: JSON.stringify({
                name,
                workflow_type,
                max_retries,
                retry_interval_minutes,
                is_active: true,
                status: 'active'
            })
        });
        
        if (response.ok) {
            closeModal('campaign-modal');
            document.getElementById('campaign-form').reset();
            loadCampaigns();
        } else {
            const err = await response.json();
            alert(`Error: ${err.detail}`);
        }
    } catch (err) {
        console.error(err);
    }
});

// Delete Campaign
async function deleteCampaign(id) {
    if (!confirm('Are you sure you want to delete this campaign?')) return;
    try {
        const response = await apiFetch(`/campaigns/${id}`, {
            method: 'DELETE'
        });
        if (response.ok) loadCampaigns();
    } catch (err) {
        console.error(err);
    }
}

// 3. Customers CRUD list
async function loadCustomers() {
    try {
        const response = await apiFetch('/customers/');
        if (!response.ok) return;
        
        const data = await response.json();
        const tbody = document.getElementById('customers-tbody');
        tbody.innerHTML = '';
        
        const customers = getArrayData(data);
        if (customers.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-muted);">No customer contacts imported.</td></tr>';
            return;
        }
        
        customers.forEach(c => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td style="font-weight: 600;">${c.first_name} ${c.last_name || ''}</td>
                <td>${c.phone_number}</td>
                <td>${c.email || '<span style="color: var(--text-muted);">N/A</span>'}</td>
                <td style="font-size: 0.8rem; color: var(--text-muted); max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                    ${JSON.stringify(c.custom_variables || {})}
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        console.error(err);
    }
}

// Import customers
document.getElementById('import-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const fileInput = document.getElementById('import-file');
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    
    try {
        const response = await apiFetch('/customers/import', {
            method: 'POST',
            body: formData
        });
        
        if (response.ok) {
            closeModal('import-modal');
            document.getElementById('import-form').reset();
            loadCustomers();
            alert('Contacts imported successfully!');
        } else {
            const err = await response.json();
            alert(`Import failed: ${err.detail}`);
        }
    } catch (err) {
        console.error(err);
    }
});

// 4. Knowledge Base list and uploads
async function loadKnowledge() {
    // Populate select campaign option
    await loadCampaigns();
    
    const campSelect = document.getElementById('upload-doc-campaign');
    const campaignId = campSelect.value;
    
    const tbody = document.getElementById('knowledge-tbody');
    tbody.innerHTML = '';
    
    if (!campaignId) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted);">Please select a campaign scope above to review documents.</td></tr>';
        return;
    }
    
    try {
        const response = await apiFetch(`/campaigns/${campaignId}/documents`);
        if (!response.ok) return;
        
        const data = await response.json();
        const docs = getArrayData(data);
        if (docs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted);">No documents indexed for this campaign.</td></tr>';
            return;
        }
        
        docs.forEach(d => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td style="font-weight: 600;">${d.filename}</td>
                <td style="text-transform: uppercase;">${d.file_type}</td>
                <td>${d.total_chunks} segments</td>
                <td><span class="badge badge-active">${d.status}</span></td>
                <td>
                    <button class="logout-btn" onclick="deleteDocument('${d.id}')">
                        <i class="fa-solid fa-trash"></i>
                    </button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        console.error(err);
    }
}

// Trigger load doc on dropdown switch
document.getElementById('upload-doc-campaign').addEventListener('change', loadKnowledge);

// Index Document
document.getElementById('upload-doc-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const campaignId = document.getElementById('upload-doc-campaign').value;
    const fileInput = document.getElementById('upload-doc-file');
    
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    
    try {
        const response = await apiFetch(`/campaigns/${campaignId}/documents`, {
            method: 'POST',
            body: formData
        });
        
        if (response.ok) {
            document.getElementById('upload-doc-file').value = '';
            loadKnowledge();
            alert('File text parsed and indexed successfully!');
        } else {
            const err = await response.json();
            alert(`Parsing failed: ${err.detail}`);
        }
    } catch (err) {
        console.error(err);
    }
});

// Delete Document
async function deleteDocument(id) {
    if (!confirm('Are you sure you want to delete this document from RAG knowledge index?')) return;
    try {
        const response = await apiFetch(`/documents/${id}`, {
            method: 'DELETE'
        });
        if (response.ok) loadKnowledge();
    } catch (err) {
        console.error(err);
    }
}

// 5. Call History Logs list
async function loadCallLogs() {
    try {
        const response = await apiFetch('/telephony/logs');
        if (!response.ok) return;
        
        const data = await response.json();
        const tbody = document.getElementById('logs-tbody');
        tbody.innerHTML = '';
        
        const logs = getArrayData(data);
        if (logs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted);">No complete call transcripts found. Start calls to view logs.</td></tr>';
            return;
        }
        
        // Map logs globally for dynamic viewing
        window._loadedLogs = window._loadedLogs || {};
        
        logs.forEach(log => {
            window._loadedLogs[log.plivo_call_uuid] = log;
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td style="font-size: 0.8rem; font-family: monospace;">${log.plivo_call_uuid || log.id}</td>
                <td>${log.phone_number}</td>
                <td><span class="badge badge-active">${log.status}</span></td>
                <td>${log.duration_seconds || 0}s</td>
                <td>
                    <button class="btn btn-primary" style="padding: 0.4rem 0.75rem; font-size: 0.8rem;" onclick="viewCallTranscript('${log.plivo_call_uuid}')">
                        <i class="fa-solid fa-align-left"></i> Review Transcript
                      </button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        console.error(err);
    }
}

// Trigger outbound call trigger
async function triggerDialCampaign(campaignId) {
    // Find a customer to dial
    try {
        const custResp = await apiFetch('/customers/');
        if (!custResp.ok) return;
        const data = await custResp.json();
        const customers = getArrayData(data);
        
        if (!customers || customers.length === 0) {
            alert('Please import customer contacts first in the Customers tab.');
            return;
        }
        
        let customer = customers[0];
        
        // Trigger dialing API
        const response = await apiFetch('/telephony/dial', {
            method: 'POST',
            body: JSON.stringify({
                campaign_id: campaignId,
                customer_id: customer.id,
                phone_number: customer.phone_number
            })
        });
        
        if (response.ok) {
            const data = await response.json();
            alert(`Call triggered successfully! Simulated Plivo Request UUID: ${data.request_uuid}`);
            
            // Add a mock active connection row to live dashboard panel
            const liveTbody = document.getElementById('live-calls-tbody');
            liveTbody.innerHTML = `
                <tr id="live-row-${data.request_uuid}">
                    <td style="font-size: 0.8rem; font-family: monospace;">${data.request_uuid}</td>
                    <td>${customer.phone_number}</td>
                    <td>Outbound Trigger</td>
                    <td><span class="badge badge-scheduled">ringing</span></td>
                    <td id="duration-${data.request_uuid}">0s</td>
                    <td>
                        <button class="btn btn-primary" style="padding: 0.3rem 0.6rem; font-size: 0.75rem;" onclick="viewLiveMockTranscript('${data.request_uuid}')">
                            <i class="fa-solid fa-align-left"></i> View Live
                        </button>
                    </td>
                </tr>
            `;
            
            // Periodically increment mock duration counter
            let sec = 0;
            const timer = setInterval(() => {
                const row = document.getElementById(`live-row-${data.request_uuid}`);
                if (!row) {
                    clearInterval(timer);
                    return;
                }
                sec += 2;
                document.getElementById(`duration-${data.request_uuid}`).innerText = `${sec}s`;
                
                // End after 10s
                if (sec >= 10) {
                    clearInterval(timer);
                    // Move to call logs database
                    row.remove();
                    liveTbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">No active voice connections at this moment.</td></tr>';
                    
                    // Call End Webhook
                    apiFetch(`/conversation/${data.request_uuid}/end`, {
                        method: 'POST',
                        body: JSON.stringify({
                            campaign_id: campaignId,
                            customer_id: customer.id,
                            phone_number: customer.phone_number,
                            duration_seconds: sec
                        })
                    }).then(() => {
                        alert('Voice call completed. Transcript saved to PostgreSQL call log.');
                        loadCallLogs(); // Refresh logs tab
                    });
                }
            }, 2000);
            
        } else {
            const err = await response.json();
            alert(`Dial failed: ${err.detail}`);
        }
    } catch (err) {
        console.error(err);
    }
}

// Real Transcript modal display
function viewCallTranscript(plivoCallUuid) {
    const log = window._loadedLogs ? window._loadedLogs[plivoCallUuid] : null;
    const viewer = document.getElementById('transcript-viewer-box');
    viewer.innerHTML = '';
    
    if (!log || !log.transcript || log.transcript.length === 0) {
        viewer.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 2rem;">No conversational text exchanges logged for this call.</div>';
    } else {
        log.transcript.forEach(ex => {
            const isAgent = (ex.sender === 'agent');
            const div = document.createElement('div');
            div.className = `chat-bubble ${isAgent ? 'agent' : 'customer'}`;
            div.style.marginTop = '10px';
            div.innerText = `${isAgent ? 'AI Coordinator' : 'Customer'}: ${ex.text}`;
            viewer.appendChild(div);
        });
    }
    openModal('transcript-modal');
}

// Live Mock Transcript modal display
function viewLiveMockTranscript(id) {
    const viewer = document.getElementById('transcript-viewer-box');
    viewer.innerHTML = `
        <div class="chat-bubble customer">Hello, who is this?</div>
        <div class="chat-bubble agent" style="margin-top: 10px;">Hello! I am James from Premium Realty. I wanted to tell you about our new listing...</div>
        <div class="chat-bubble customer" style="margin-top: 10px;">Great, can I book an appointment?</div>
        <div class="chat-bubble agent" style="margin-top: 10px;">Sure! Let me schedule that showing details...</div>
    `;
    openModal('transcript-modal');
}
