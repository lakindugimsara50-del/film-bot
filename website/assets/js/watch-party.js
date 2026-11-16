/**
 * FilmZilla – Synchronized Watch Party Engine (v1.0)
 * Allows friends to watch movies together remotely with synchronized playback,
 * live in-room chat, floating reactions, and Dual Control Mode (Host Lock vs Collaborative).
 */

'use strict';

(function(window) {

  const WP_CONFIG = {
    syncToleranceSeconds: 2.5,
    peerPrefix: 'fz_room_'
  };

  const WP_3D_EMOJIS = {
    fire: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Fire/3D/fire_3d.png',
    mindblown: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Exploding%20head/3D/exploding_head_3d.png',
    popcorn: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Popcorn/3D/popcorn_3d.png',
    angry: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Angry%20face/3D/angry_face_3d.png',
    heart: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Red%20heart/3D/red_heart_3d.png',
    laugh: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Rolling%20on%20the%20floor%20laughing/3D/rolling_on_the_floor_laughing_3d.png'
  };

  class WatchPartyEngine {
    constructor() {
      this.roomId = null;
      this.isHost = false;
      this.isLocked = false; // Dual Control: true = Host only, false = Collaborative (anyone can control)
      this.localUser = this.getLocalUser();
      this.members = new Map(); // id -> { name, isHost, avatar, status }
      this.bc = null;
      this.peer = null;
      this.peerConnections = new Map();
      this.vjsPlayer = null;
      this.isInternalSeeking = false;
      this.lastBroadcastActionTime = 0;

      this.initFromUrl();
    }

    getLocalUser() {
      if (window.FilmSubUser && typeof window.FilmSubUser.getUser === 'function') {
        const u = window.FilmSubUser.getUser();
        return {
          id: u.id || ('usr_' + Math.random().toString(36).substring(2, 9)),
          name: u.name || 'Friend',
          avatar: u.avatar || 'assets/img/filmzilla_icon.png'
        };
      }
      return {
        id: 'usr_' + Math.random().toString(36).substring(2, 9),
        name: 'Cinema Guest',
        avatar: 'assets/img/filmzilla_icon.png'
      };
    }

    initFromUrl() {
      const urlParams = new URLSearchParams(window.location.search);
      const roomParam = urlParams.get('room');
      if (roomParam) {
        this.roomId = roomParam;
        this.isHost = false;
        // Check if stored as host in session
        if (sessionStorage.getItem(`fz_host_${this.roomId}`) === 'true') {
          this.isHost = true;
        }
      }
    }

    // Attach Video.js player reference
    bindPlayer(player) {
      if (!player) return;
      this.vjsPlayer = player;

      // Listen to player events for synchronization
      player.on('play', () => {
        if (this.isInternalSeeking) return;
        this.handleLocalPlay();
      });

      player.on('pause', () => {
        if (this.isInternalSeeking) return;
        this.handleLocalPause();
      });

      player.on('seeked', () => {
        if (this.isInternalSeeking) return;
        this.handleLocalSeek();
      });

      player.on('waiting', () => {
        this.broadcastState('buffering');
      });

      player.on('playing', () => {
        this.broadcastState('playing');
      });

      // If already in a room upon load, activate UI
      if (this.roomId) {
        this.joinRoom(this.roomId, this.isHost);
      }
    }

    createRoom() {
      const rand = Math.random().toString(36).substring(2, 8).toUpperCase();
      this.roomId = `WP-${rand}`;
      this.isHost = true;
      this.isLocked = false; // Default: Collaborative as requested by user
      sessionStorage.setItem(`fz_host_${this.roomId}`, 'true');

      // Update URL without reload
      const newUrl = new URL(window.location.href);
      newUrl.searchParams.set('room', this.roomId);
      window.history.replaceState({}, '', newUrl.toString());

      this.joinRoom(this.roomId, true);
      this.showActionToast(`Watch Party room created: ${this.roomId} 🍿`);
    }

    joinRoom(roomId, isHost = false) {
      this.roomId = roomId;
      this.isHost = isHost;

      // 1. BroadcastChannel (fast local cross-tab sync)
      try {
        if (typeof BroadcastChannel !== 'undefined') {
          this.bc = new BroadcastChannel(`fz_wp_${this.roomId}`);
          this.bc.onmessage = (e) => this.handleMessage(e.data);
        }
      } catch (err) {
        console.warn('BroadcastChannel not supported:', err);
      }

      // 2. PeerJS (remote peer-to-peer sync across devices)
      this.initPeerJS();

      // 3. Render UI container
      this.renderWatchPartyUI();

      // 4. Announce self to room
      this.broadcast({
        type: 'MEMBER_JOIN',
        user: this.localUser,
        isHost: this.isHost,
        timestamp: Date.now()
      });

      // Add local user to members map
      this.members.set(this.localUser.id, {
        name: this.localUser.name,
        isHost: this.isHost,
        status: 'ready'
      });
      this.updateMembersList();

      // If host, periodically broadcast heartbeat sync
      if (this.isHost) {
        this.startHeartbeat();
      }
    }

    initPeerJS() {
      if (typeof window.Peer === 'undefined') return;

      try {
        // Deterministic peer id or random
        const myPeerId = this.isHost ? `${WP_CONFIG.peerPrefix}${this.roomId}_host` : `${WP_CONFIG.peerPrefix}${this.roomId}_${this.localUser.id}`;
        this.peer = new window.Peer(myPeerId, {
          debug: 0
        });

        this.peer.on('open', (id) => {
          // If not host, connect to host peer
          if (!this.isHost) {
            const hostPeerId = `${WP_CONFIG.peerPrefix}${this.roomId}_host`;
            const conn = this.peer.connect(hostPeerId);
            if (conn) {
              this.setupPeerConnection(conn);
            }
          }
        });

        this.peer.on('connection', (conn) => {
          this.setupPeerConnection(conn);
        });

        this.peer.on('error', (err) => {
          // Host peer ID might be taken if reconnecting, fallback gracefully
          console.log('PeerJS note:', err.type);
        });
      } catch (e) {
        console.warn('Peer init err:', e);
      }
    }

    setupPeerConnection(conn) {
      conn.on('open', () => {
        this.peerConnections.set(conn.peer, conn);
        // Send initial greeting
        conn.send({
          type: 'MEMBER_JOIN',
          user: this.localUser,
          isHost: this.isHost,
          timestamp: Date.now()
        });
      });

      conn.on('data', (data) => {
        this.handleMessage(data);
      });

      conn.on('close', () => {
        this.peerConnections.delete(conn.peer);
      });
    }

    startHeartbeat() {
      if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = setInterval(() => {
        if (!this.vjsPlayer || !this.isHost) return;
        const curTime = typeof this.vjsPlayer.currentTime === 'function' ? this.vjsPlayer.currentTime() : 0;
        const isPaused = typeof this.vjsPlayer.paused === 'function' ? this.vjsPlayer.paused() : true;

        this.broadcast({
          type: 'SYNC_HEARTBEAT',
          currentTime: curTime,
          isPaused: isPaused,
          isLocked: this.isLocked,
          timestamp: Date.now()
        });
      }, 3000);
    }

    broadcast(payload) {
      // 1. BroadcastChannel
      if (this.bc) {
        try { this.bc.postMessage(payload); } catch (_) {}
      }

      // 2. PeerJS connections
      for (const conn of this.peerConnections.values()) {
        if (conn.open) {
          try { conn.send(payload); } catch (_) {}
        }
      }
    }

    handleMessage(data) {
      if (!data || !data.type) return;

      switch (data.type) {
        case 'MEMBER_JOIN':
          if (data.user) {
            this.members.set(data.user.id, {
              name: data.user.name,
              isHost: data.isHost,
              status: 'ready'
            });
            this.updateMembersList();
            this.appendChatMessage('System', `${data.user.name} joined the Watch Party! 🎉`, false, true);

            // If we are host, respond with our current playback state and lock state
            if (this.isHost && this.vjsPlayer) {
              this.broadcast({
                type: 'SYNC_STATE',
                currentTime: this.vjsPlayer.currentTime(),
                isPaused: this.vjsPlayer.paused(),
                isLocked: this.isLocked,
                targetUserId: data.user.id
              });
            }
          }
          break;

        case 'SYNC_STATE':
        case 'SYNC_HEARTBEAT':
          this.handleSyncState(data);
          break;

        case 'PLAYER_ACTION':
          this.handleRemoteAction(data);
          break;

        case 'LOCK_MODE_CHANGED':
          this.isLocked = !!data.isLocked;
          this.updateLockUI();
          this.showActionToast(this.isLocked ? 'Host locked playback controls 🔒' : 'Collaborative mode: anyone can control 👥');
          break;

        case 'CHAT_MESSAGE':
          if (data.text) {
            this.appendChatMessage(data.userName || 'Friend', data.text, false);
          }
          break;

        case 'REACTION_EMOJI':
          if (data.emoji) {
            this.showFloatingReaction(data.emoji, data.userName);
          }
          break;

        case 'MEMBER_STATUS':
          if (data.userId && this.members.has(data.userId)) {
            const m = this.members.get(data.userId);
            m.status = data.status;
            this.updateMembersList();
          }
          break;
      }
    }

    // --- Synchronization Logic ---
    handleLocalPlay() {
      if (this.isLocked && !this.isHost) {
        this.showActionToast('Host has locked playback. Only host can Play/Pause 🔒');
        if (this.vjsPlayer) {
          this.isInternalSeeking = true;
          this.vjsPlayer.pause();
          setTimeout(() => { this.isInternalSeeking = false; }, 200);
        }
        return;
      }

      this.broadcast({
        type: 'PLAYER_ACTION',
        action: 'play',
        currentTime: this.vjsPlayer ? this.vjsPlayer.currentTime() : 0,
        userName: this.localUser.name,
        timestamp: Date.now()
      });
      this.showActionToast(`${this.localUser.name} played playback ▶️`);
    }

    handleLocalPause() {
      if (this.isLocked && !this.isHost) {
        this.showActionToast('Host has locked playback. Only host can Play/Pause 🔒');
        if (this.vjsPlayer) {
          this.isInternalSeeking = true;
          this.vjsPlayer.play();
          setTimeout(() => { this.isInternalSeeking = false; }, 200);
        }
        return;
      }

      this.broadcast({
        type: 'PLAYER_ACTION',
        action: 'pause',
        currentTime: this.vjsPlayer ? this.vjsPlayer.currentTime() : 0,
        userName: this.localUser.name,
        timestamp: Date.now()
      });
      this.showActionToast(`${this.localUser.name} paused playback ⏸️`);
    }

    handleLocalSeek() {
      if (this.isLocked && !this.isHost) return;

      const now = Date.now();
      if (now - this.lastBroadcastActionTime < 500) return;
      this.lastBroadcastActionTime = now;

      const t = this.vjsPlayer ? this.vjsPlayer.currentTime() : 0;
      this.broadcast({
        type: 'PLAYER_ACTION',
        action: 'seek',
        currentTime: t,
        userName: this.localUser.name,
        timestamp: now
      });
      const min = Math.floor(t / 60);
      const sec = Math.floor(t % 60);
      this.showActionToast(`${this.localUser.name} jumped to ${min}:${sec < 10 ? '0' : ''}${sec} ⏩`);
    }

    handleRemoteAction(data) {
      if (!this.vjsPlayer) return;

      this.isInternalSeeking = true;
      const targetTime = typeof data.currentTime === 'number' ? data.currentTime : null;

      if (targetTime !== null && Math.abs(this.vjsPlayer.currentTime() - targetTime) > WP_CONFIG.syncToleranceSeconds) {
        this.vjsPlayer.currentTime(targetTime);
      }

      if (data.action === 'play') {
        this.vjsPlayer.play();
        this.showActionToast(`${data.userName || 'A friend'} played playback ▶️`);
      } else if (data.action === 'pause') {
        this.vjsPlayer.pause();
        this.showActionToast(`${data.userName || 'A friend'} paused playback ⏸️`);
      } else if (data.action === 'seek' && targetTime !== null) {
        const min = Math.floor(targetTime / 60);
        const sec = Math.floor(targetTime % 60);
        this.showActionToast(`${data.userName || 'A friend'} jumped to ${min}:${sec < 10 ? '0' : ''}${sec} ⏩`);
      }

      setTimeout(() => {
        this.isInternalSeeking = false;
      }, 300);
    }

    handleSyncState(data) {
      if (!this.vjsPlayer) return;

      if (typeof data.isLocked === 'boolean') {
        this.isLocked = data.isLocked;
        this.updateLockUI();
      }

      const curLocal = this.vjsPlayer.currentTime();
      const curRemote = data.currentTime;
      const drift = Math.abs(curLocal - curRemote);

      // If drift is substantial, quietly realign
      if (drift > WP_CONFIG.syncToleranceSeconds) {
        this.isInternalSeeking = true;
        this.vjsPlayer.currentTime(curRemote);
        setTimeout(() => { this.isInternalSeeking = false; }, 300);
      }

      // Sync pause/play state if different
      if (data.isPaused && !this.vjsPlayer.paused()) {
        this.isInternalSeeking = true;
        this.vjsPlayer.pause();
        setTimeout(() => { this.isInternalSeeking = false; }, 300);
      } else if (!data.isPaused && this.vjsPlayer.paused()) {
        this.isInternalSeeking = true;
        this.vjsPlayer.play();
        setTimeout(() => { this.isInternalSeeking = false; }, 300);
      }
    }

    broadcastState(status) {
      this.broadcast({
        type: 'MEMBER_STATUS',
        userId: this.localUser.id,
        status: status
      });
    }

    toggleLockMode() {
      if (!this.isHost) {
        this.showActionToast('Only the Watch Party host can change room control modes 🔒');
        return;
      }

      this.isLocked = !this.isLocked;
      this.updateLockUI();
      this.broadcast({
        type: 'LOCK_MODE_CHANGED',
        isLocked: this.isLocked,
        userName: this.localUser.name
      });
      this.showActionToast(this.isLocked ? 'Playback locked: Host-Only mode 🔒' : 'Collaborative mode: Everyone can control 👥');
    }

    createOrToggleRoom() {
      if (!this.roomId) {
        this.createRoom();
      } else {
        const container = document.getElementById('watch-party-container');
        const theater = document.getElementById('cs-theater-layout');
        if (container && theater) {
          const isClosed = container.style.display === 'none';
          if (isClosed) {
            theater.classList.add('is-watch-party-active');
            container.style.display = 'flex';
          } else {
            theater.classList.remove('is-watch-party-active');
            container.style.display = 'none';
          }
        }
      }
    }

    // --- UI Renderers & Interactions ---
    renderWatchPartyUI() {
      let container = document.getElementById('watch-party-container');
      const theater = document.getElementById('cs-theater-layout');
      if (theater) theater.classList.add('is-watch-party-active');

      if (!container) {
        const playerSec = document.querySelector('.cs-player-card');
        if (!playerSec) return;

        container = document.createElement('aside');
        container.id = 'watch-party-container';
        container.className = 'watch-party-container';
        playerSec.parentNode.insertBefore(container, playerSec.nextSibling);
      }

      container.style.display = 'flex';

      // Update Flagship button in menu bar
      const wpBtn = document.getElementById('btn-watch-party-trigger');
      if (wpBtn) {
        wpBtn.classList.add('active-room');
        const badge = wpBtn.querySelector('#cs-party-badge');
        if (badge) badge.textContent = 'ACTIVE';
      }

      container.innerHTML = `
        <div class="wp-header">
          <div class="wp-title-wrap">
            <div class="wp-title">
              <i class="fa-solid fa-crown" style="color:#f59e0b"></i>
              <span class="wp-vip-title">VIP WATCH PARTY</span>
            </div>
            <span class="wp-room-badge" id="wp-room-code" title="Click to copy room link">${this.roomId} <i class="fa-regular fa-copy" style="font-size:9.5px"></i></span>
          </div>
          <div class="wp-header-controls">
            ${this.isHost ? `
              <button type="button" class="wp-lock-toggle ${this.isLocked ? 'locked' : ''}" id="wp-lock-toggle" title="Click to toggle control mode">
                <i class="fa-solid ${this.isLocked ? 'fa-lock' : 'fa-lock-open'}"></i>
                <span id="wp-lock-label">${this.isLocked ? 'Host Only' : 'Everyone Controls'}</span>
              </button>
            ` : `
              <span class="wp-lock-toggle ${this.isLocked ? 'locked' : ''}" style="cursor:default">
                <i class="fa-solid ${this.isLocked ? 'fa-lock' : 'fa-lock-open'}"></i>
                <span>${this.isLocked ? 'Host Only' : 'Everyone Controls'}</span>
              </span>
            `}
            <button type="button" class="wp-close-btn" id="wp-close-btn" title="Minimize Lounge">
              <i class="fa-solid fa-xmark"></i>
            </button>
          </div>
        </div>

        <!-- Pinned Room Broadcast Card -->
        <div class="wp-pinned-announcement">
          <div class="wp-announcement-header">
            <span class="wp-live-pulse-dot"></span>
            <span class="wp-announcement-title">Cinema Live Synchronizer</span>
            <span class="wp-sync-pill">🟢 100% Synced</span>
          </div>
          <p class="wp-announcement-desc">You are streaming in real-time. Pauses, play and skips synchronize across all screens.</p>
        </div>

        <div class="wp-members-bar" id="wp-members-bar">
          <!-- Populated by updateMembersList -->
        </div>

        <div class="wp-body">
          <div class="wp-chat-box" id="wp-chat-box">
            <div class="wp-msg-item" style="background:rgba(255,255,255,0.03);align-self:center;text-align:center;max-width:95%">
              <span class="wp-msg-text" style="font-size:12px;color:#94a3b8">
                🍿 Welcome to the VIP Watch Party! Stream with friends in perfect sync.
              </span>
            </div>
          </div>

          <!-- Quick Reaction Chat Prompts -->
          <div class="wp-quick-prompts-bar">
            <button type="button" class="wp-quick-prompt" data-text="🍿 What a scene!">🍿 What a scene!</button>
            <button type="button" class="wp-quick-prompt" data-text="🔥 Pure Cinema!">🔥 Pure Cinema!</button>
            <button type="button" class="wp-quick-prompt" data-text="🤯 Plot twist!">🤯 Plot twist!</button>
            <button type="button" class="wp-quick-prompt" data-text="❤️ Goosebumps!">❤️ Goosebumps!</button>
          </div>

          <!-- 3D Fluent Emojis Reaction Deck -->
          <div class="wp-floating-reactions-bar">
            <button type="button" class="wp-reaction-chip" data-emoji="fire" title="Fire">
              <img src="https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Fire/3D/fire_3d.png" alt="Fire" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="mindblown" title="Mind Blown">
              <img src="https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Exploding%20head/3D/exploding_head_3d.png" alt="Mind Blown" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="popcorn" title="Popcorn">
              <img src="https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Popcorn/3D/popcorn_3d.png" alt="Popcorn" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="angry" title="Angry / Intense">
              <img src="https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Angry%20face/3D/angry_face_3d.png" alt="Angry" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="heart" title="Love">
              <img src="https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Red%20heart/3D/red_heart_3d.png" alt="Heart" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="laugh" title="Laugh">
              <img src="https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Rolling%20on%20the%20floor%20laughing/3D/rolling_on_the_floor_laughing_3d.png" alt="Laugh" class="react-3d-emoji" loading="lazy">
            </button>
          </div>

          <form class="wp-chat-input-bar" id="wp-chat-form">
            <input type="text" class="wp-chat-input" id="wp-chat-input" placeholder="Chat with room..." autocomplete="off">
            <button type="submit" class="wp-btn-send" id="wp-btn-send" aria-label="Send message">
              <i class="fa-solid fa-paper-plane"></i>
            </button>
          </form>
        </div>

        <div class="wp-footer-share">
          <button type="button" class="wp-btn-wa-invite" id="wp-share-btn">
            <i class="fa-brands fa-whatsapp"></i> Invite on WhatsApp
          </button>
          <button type="button" class="wp-btn-copy-link" id="wp-copy-link-btn" title="Copy Invite Link">
            <i class="fa-solid fa-link"></i>
          </button>
        </div>
      `;

      // Wire Room Code Copy
      const roomBadge = container.querySelector('#wp-room-code');
      if (roomBadge) {
        roomBadge.addEventListener('click', () => this.shareInviteLink());
      }

      // Wire Close/Minimize
      const closeBtn = container.querySelector('#wp-close-btn');
      if (closeBtn) {
        closeBtn.addEventListener('click', () => {
          if (theater) theater.classList.remove('is-watch-party-active');
          container.style.display = 'none';
          this.showActionToast('Watch Party minimized. Click "Room Active" to re-open 👥');
        });
      }

      // Wire Lock Toggle
      const lockToggle = container.querySelector('#wp-lock-toggle');
      if (lockToggle && this.isHost) {
        lockToggle.addEventListener('click', () => this.toggleLockMode());
      }

      // Wire WhatsApp Share Invite
      const shareBtn = container.querySelector('#wp-share-btn');
      if (shareBtn) {
        shareBtn.addEventListener('click', () => this.shareInviteLink());
      }

      // Wire Copy Link Button
      const copyBtn = container.querySelector('#wp-copy-link-btn');
      if (copyBtn) {
        copyBtn.addEventListener('click', () => {
          if (navigator.clipboard) {
            navigator.clipboard.writeText(window.location.href);
            this.showActionToast('Watch Party link copied to clipboard! 📋');
          }
        });
      }

      // Wire Chat Form
      const chatForm = container.querySelector('#wp-chat-form');
      const chatInput = container.querySelector('#wp-chat-input');
      if (chatForm && chatInput) {
        chatForm.addEventListener('submit', (e) => {
          e.preventDefault();
          const text = chatInput.value.trim();
          if (!text) return;
          chatInput.value = '';

          this.appendChatMessage(this.localUser.name, text, true);
          this.broadcast({
            type: 'CHAT_MESSAGE',
            userName: this.localUser.name,
            text: text,
            timestamp: Date.now()
          });
        });
      }

      // Wire Reactions
      container.querySelectorAll('.wp-reaction-chip').forEach(btn => {
        btn.addEventListener('click', () => {
          const emoji = btn.dataset.emoji;
          if (!emoji) return;
          this.showFloatingReaction(emoji, this.localUser.name);
          this.broadcast({
            type: 'REACTION_EMOJI',
            emoji: emoji,
            userName: this.localUser.name
          });
        });
      });

      // Wire Quick Reaction Prompts
      container.querySelectorAll('.wp-quick-prompt').forEach(btn => {
        btn.addEventListener('click', () => {
          const promptText = btn.textContent.trim();
          if (!promptText) return;
          this.appendChatMessage(this.localUser.name, promptText, true);
          this.broadcast({
            type: 'CHAT_MESSAGE',
            userName: this.localUser.name,
            text: promptText,
            timestamp: Date.now()
          });
        });
      });
    }

    updateLockUI() {
      const lockToggle = document.getElementById('wp-lock-toggle');
      const lockLabel = document.getElementById('wp-lock-label');
      if (lockToggle) {
        lockToggle.classList.toggle('locked', this.isLocked);
        const icon = lockToggle.querySelector('i');
        if (icon) {
          icon.className = `fa-solid ${this.isLocked ? 'fa-lock' : 'fa-lock-open'}`;
        }
      }
      if (lockLabel) {
        lockLabel.textContent = this.isLocked ? 'Host Only' : 'Everyone Controls';
      }
    }

    updateMembersList() {
      const bar = document.getElementById('wp-members-bar');
      if (!bar) return;

      const html = [];
      for (const [id, m] of this.members.entries()) {
        const isMe = id === this.localUser.id;
        const isHost = m.isHost;
        html.push(`
          <div class="wp-member-pill">
            <span class="wp-status-dot ${m.status === 'buffering' ? 'buffering' : ''}"></span>
            <span>${m.name}${isMe ? ' (You)' : ''}</span>
            ${isHost ? '<span style="font-size:10px;background:#8a2be2;padding:1px 6px;border-radius:999px;font-weight:700">Host</span>' : ''}
          </div>
        `);
      }
      bar.innerHTML = html.join('');
    }

    appendChatMessage(author, text, isMe = false, isSystem = false) {
      const chatBox = document.getElementById('wp-chat-box');
      if (!chatBox) return;

      const item = document.createElement('div');
      item.className = `wp-msg-item ${isMe ? 'me' : ''}`;
      if (isSystem) {
        item.style.background = 'rgba(138, 43, 226, 0.15)';
        item.style.alignSelf = 'center';
      }

      item.innerHTML = `
        <span class="wp-msg-author">${author}</span>
        <span class="wp-msg-text">${this.escapeHtml(text)}</span>
      `;
      chatBox.appendChild(item);
      chatBox.scrollTop = chatBox.scrollHeight;
    }

    showFloatingReaction(emoji, userName) {
      const playerWrap = document.querySelector('.player-container') || document.body;
      const emojiKey = String(emoji).toLowerCase();
      const emojiMap = {
        '🔥': 'fire',
        '🤯': 'mindblown',
        '🍿': 'popcorn',
        '😡': 'angry',
        '❤️': 'heart',
        '😂': 'laugh',
        'fire': 'fire',
        'mindblown': 'mindblown',
        'popcorn': 'popcorn',
        'angry': 'angry',
        'heart': 'heart',
        'laugh': 'laugh'
      };
      const resolvedKey = emojiMap[emojiKey] || emojiMap[emoji] || 'fire';
      const src = WP_3D_EMOJIS[resolvedKey] || WP_3D_EMOJIS.fire;

      const pRect = playerWrap.getBoundingClientRect();
      const baseLeft = pRect.left + (Math.random() * (pRect.width * 0.6)) + (pRect.width * 0.2);
      const baseTop = Math.max(100, pRect.bottom - 50);

      // Spawn 3 organic 3D floating particles
      for (let i = 0; i < 3; i++) {
        const particle = document.createElement('img');
        particle.className = 'floating-3d-particle';
        particle.src = src;
        particle.alt = resolvedKey;
        particle.style.left = `${baseLeft + (Math.random() * 40 - 20)}px`;
        particle.style.top = `${baseTop}px`;
        particle.style.setProperty('--rand-x', `${(Math.random() - 0.5) * 120}px`);
        particle.style.setProperty('--rand-rot', `${(Math.random() - 0.5) * 50}deg`);
        particle.style.animationDelay = `${i * 100}ms`;

        document.body.appendChild(particle);
        setTimeout(() => {
          if (particle.parentNode) particle.parentNode.removeChild(particle);
        }, 1800);
      }
    }

    showActionToast(text) {
      let toast = document.getElementById('wp-action-toast');
      if (!toast) {
        toast = document.createElement('div');
        toast.id = 'wp-action-toast';
        toast.className = 'wp-action-toast';
        document.body.appendChild(toast);
      }

      toast.textContent = text;
      toast.classList.add('show');

      if (this.toastTimeout) clearTimeout(this.toastTimeout);
      this.toastTimeout = setTimeout(() => {
        toast.classList.remove('show');
      }, 2400);
    }

    shareInviteLink() {
      const shareUrl = window.location.href;
      const movieTitle = document.getElementById('movie-detail-title')?.textContent || 'this movie';
      const text = `🍿 Join my Watch Party on FilmZilla to watch "${movieTitle}" with me in sync!\n\nLink: ${shareUrl}`;

      if (navigator.share) {
        navigator.share({
          title: `FilmZilla Watch Party – ${movieTitle}`,
          text: text,
          url: shareUrl
        }).catch(() => {});
      } else {
        const waUrl = `https://api.whatsapp.com/send?text=${encodeURIComponent(text)}`;
        window.open(waUrl, '_blank', 'noopener');
        if (navigator.clipboard) {
          navigator.clipboard.writeText(shareUrl);
          this.showActionToast('Watch Party link copied to clipboard! 📋');
        }
      }
    }

    escapeHtml(str) {
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }
  }

  // Instantiate and expose globally
  window.FilmZillaWatchParty = new WatchPartyEngine();

  // Wire auto-bind when player is initialized
  document.addEventListener('DOMContentLoaded', () => {
    // If room already in URL, setup UI container immediately
    if (window.FilmZillaWatchParty.roomId) {
      window.FilmZillaWatchParty.renderWatchPartyUI();
    }
  });

})(window);
