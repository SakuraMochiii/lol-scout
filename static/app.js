/* LoL Scout — client-side interactions */

function togglePlayer(headerEl) {
  const card = headerEl.closest('.player-card');
  const body = card.querySelector('.player-body');
  const arrow = card.querySelector('.expand-arrow');
  const isOpen = body.style.display !== 'none';
  body.style.display = isOpen ? 'none' : 'block';
  arrow.classList.toggle('open', !isOpen);
}

async function refreshPlayer(playerId, btn) {
  if (btn) {
    btn.textContent = 'Refreshing...';
    btn.disabled = true;
  }
  try {
    const res = await fetch(`/api/players/${playerId}/refresh`, { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      location.reload();
    } else {
      alert('Refresh failed: ' + (data.error || 'Unknown error'));
    }
  } catch (e) {
    alert('Network error: ' + e.message);
  } finally {
    if (btn) {
      btn.textContent = 'Refresh';
      btn.disabled = false;
    }
  }
}

async function refreshTeam(teamId) {
  _startRefresh(teamId, 'refresh-status', true);
}

async function refreshTeamManage(teamId, btn) {
  btn.disabled = true;
  btn.textContent = 'Refreshing...';
  _startRefresh(teamId, 'refresh-status-' + teamId, false);
}

async function _startRefresh(teamId, statusId, reloadOnDone) {
  const status = document.getElementById(statusId);
  status.style.display = 'block';
  status.textContent = 'Starting refresh...';

  try {
    const res = await fetch(`/api/teams/${teamId}/refresh`, { method: 'POST' });
    const data = await res.json();
    if (!data.success) {
      status.textContent = 'Refresh failed: ' + (data.error || 'Unknown error');
      return;
    }
    if (data.status === 'already_running') {
      status.textContent = 'Refresh already in progress...';
    }
    pollRefreshStatus(teamId, statusId, reloadOnDone);
  } catch (e) {
    status.textContent = 'Network error: ' + e.message;
  }
}

async function pollRefreshStatus(teamId, statusId, reloadOnDone) {
  const status = document.getElementById(statusId);
  try {
    const res = await fetch(`/api/teams/${teamId}/refresh/status`);
    const job = await res.json();
    if (job.status === 'none') {
      status.textContent = 'No refresh job found.';
      return;
    }
    const pct = job.total > 0 ? Math.round(job.done / job.total * 100) : 0;
    const current = job.current ? ` — ${job.current}` : '';
    status.textContent = `Refreshing ${job.done}/${job.total} (${pct}%)${current}`;

    if (job.status === 'complete') {
      const failed = (job.results || []).filter(r => !r.success);
      if (failed.length > 0) {
        status.textContent = `Done. ${failed.length} failed: ${failed.map(f => f.player).join(', ')}`;
      } else {
        status.textContent = `All ${job.total} players refreshed.`;
      }
      if (reloadOnDone) setTimeout(() => location.reload(), 1500);
      return;
    }
    setTimeout(() => pollRefreshStatus(teamId, statusId, reloadOnDone), 2000);
  } catch (e) {
    setTimeout(() => pollRefreshStatus(teamId, statusId, reloadOnDone), 3000);
  }
}

async function deletePlayer(playerId) {
  if (!confirm('Remove this player?')) return;
  try {
    const res = await fetch(`/api/players/${playerId}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.success) location.reload();
    else alert('Failed: ' + (data.error || 'Unknown error'));
  } catch (e) {
    alert('Network error: ' + e.message);
  }
}

async function addTeam() {
  const input = document.getElementById('new-team-name');
  const name = input.value.trim();
  if (!name) return;
  try {
    const res = await fetch('/api/teams', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    const data = await res.json();
    if (data.success) location.reload();
    else alert('Failed: ' + (data.error || 'Unknown error'));
  } catch (e) {
    alert('Network error: ' + e.message);
  }
}

async function deleteTeam(teamId, teamName) {
  if (!confirm(`Delete team "${teamName}" and all its players?`)) return;
  try {
    const res = await fetch(`/api/teams/${teamId}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.success) location.reload();
    else alert('Failed: ' + (data.error || 'Unknown error'));
  } catch (e) {
    alert('Network error: ' + e.message);
  }
}

async function renameTeam(teamId, newName) {
  newName = newName.trim();
  if (!newName) return;
  try {
    await fetch(`/api/teams/${teamId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newName }),
    });
  } catch (e) {
    alert('Network error: ' + e.message);
  }
}

async function setMyTeam(teamId) {
  try {
    const res = await fetch(`/api/teams/${teamId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ set_my_team: true }),
    });
    const data = await res.json();
    if (data.success) location.reload();
  } catch (e) {
    alert('Network error: ' + e.message);
  }
}

async function addPlayer(teamId) {
  const input = document.querySelector(`.player-input[data-team="${teamId}"]`);
  const roleSelect = document.querySelector(`.role-select[data-team="${teamId}"]`);
  const subCheck = document.querySelector(`.sub-check[data-team="${teamId}"]`);
  const overwriteCheck = document.querySelector(`.overwrite-check[data-team="${teamId}"]`);

  const playerInput = input.value.trim();
  if (!playerInput) return;

  try {
    const res = await fetch('/api/players', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        team_id: teamId,
        player_input: playerInput,
        role: roleSelect.value,
        is_substitute: subCheck.checked,
        overwrite: overwriteCheck.checked,
      }),
    });
    const data = await res.json();
    if (data.success) {
      input.value = '';
      location.reload();
    } else {
      alert('Failed: ' + (data.error || 'Unknown error'));
    }
  } catch (e) {
    alert('Network error: ' + e.message);
  }
}

async function updatePlayerExtra(playerId, field, value) {
  try {
    await fetch(`/api/players/${playerId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ extra: { [field]: value } }),
    });
  } catch (e) {
    alert('Network error: ' + e.message);
  }
}

async function updatePlayerRole(playerId, role) {
  try {
    await fetch(`/api/players/${playerId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role }),
    });
  } catch (e) {
    alert('Network error: ' + e.message);
  }
}

async function updateSeason() {
  const name = document.getElementById('season-name').value.trim();
  if (!name) return;
  try {
    const res = await fetch('/api/season', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ season_name: name }),
    });
    if (res.ok) location.reload();
  } catch (e) {
    alert('Network error: ' + e.message);
  }
}
