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
    fire: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f525/512.webp',
    popcorn: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f37f/512.webp',
    heart: 'https://fonts.gstatic.com/s/e/notoemoji/latest/2764_fe0f/512.webp',
    mindblown: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f92f/512.webp',
    laugh: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f923/512.webp',
    party: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f389/512.webp',
    shock: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f631/512.webp',
    clap: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f44f/512.webp',
    bolt: 'https://fonts.gstatic.com/s/e/notoemoji/latest/26a1/512.webp',
    skull: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f480/512.webp',
    crown: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f451/512.webp',
    star: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f929/512.webp'
  };

  const WP_CINEMA_STICKERS = [
    { key: 'popcorn', name: 'Popcorn Blast', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Popcorn/3D/popcorn_3d.png' },
    { key: 'trophy', name: 'Oscar 10/10', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Trophy/3D/trophy_3d.png' },
    { key: 'clapper', name: 'Action Clapper', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Clapper%20board/3D/clapper_board_3d.png' },
    { key: 'ticket', name: 'VIP Ticket', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Admission%20tickets/3D/admission_tickets_3d.png' },
    { key: 'bolt', name: 'Pure Hype', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/High%20voltage/3D/high_voltage_3d.png' },
    { key: 'skull', name: 'Plot Twist', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Skull/3D/skull_3d.png' },
    { key: 'crown', name: 'GOAT Movie', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Crown/3D/crown_3d.png' },
    { key: 'pizza', name: 'Snack Break', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Pizza/3D/pizza_3d.png' },
    { key: 'rocket', name: 'To The Moon', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Rocket/3D/rocket_3d.png' },
    { key: 'love', name: 'Pure Romance', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Heart%20with%20ribbon/3D/heart_with_ribbon_3d.png' }
  ];

  const WP_QUICK_EMOJIS = [
    '😀', '😂', '🤣', '😍', '🤩', '😎', '🥳', '🥺', '😱', '🤯',
    '🍿', '🎬', '🎟️', '🥤', '🔥', '❤️', '⚡', '💯', '👑', '💀',
    '👍', '👏', '🙌', '✌️', '🤙', '🤝', '🍕', '🍻', '✨', '💤'
  ];

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
      this.chatPopupsEnabled = localStorage.getItem('fz_chat_popups_enabled') !== 'false';

      // Couple Cam & Voice Chat WebRTC state
      this.localStream = null;
      this.remoteStreams = new Map(); // peerId -> MediaStream
      this.mediaCalls = new Map(); // peerId -> MediaCall
      this.remoteMediaStatus = new Map(); // userId -> { isMicOn, isCamOn, userName }
      this.isMicOn = false;
      this.isCamOn = false;
      this.isCoupleCamCollapsed = false;
      this.audioContext = null;

      // Long-Distance Couple Mode, Hugs & Touch Sync
      this.isCoupleMode = false;
      this.isWhisperMode = false;
      this.hugInterval = null;
      this.hugProgress = 0;

      // In-Party Live Predictions & Betting Arena
      this.activePrediction = null;
      this.popcornCoins = parseInt(localStorage.getItem('fz_popcorn_coins') || '250', 10);
      this.predictionLeaderboard = new Map(); // userName -> coins

      this.initFromUrl();
      this.initFullscreenListener();
      this.initKeyboardShortcuts();
      this.initCoupleTouchListener();
    }

    initFullscreenListener() {
      const syncOverlayWithFullscreen = () => {
        const fsElem = document.fullscreenElement || document.webkitFullscreenElement;
        const targetParent = fsElem || document.getElementById('filmsubPlayer') || document.getElementById('video-player-container') || document.querySelector('.player-container');
        if (!targetParent) return;

        // Move Chat Overlay
        const overlay = document.getElementById('cs-video-chat-overlay');
        if (overlay && !targetParent.contains(overlay)) {
          targetParent.appendChild(overlay);
        }

        // Move Couple Cam Dock
        const camDock = document.getElementById('cs-couple-cam-dock');
        if (camDock && !targetParent.contains(camDock)) {
          targetParent.appendChild(camDock);
        }

        // Move Floating Emoji Stage
        const stage = document.getElementById('cs-reaction-floating-stage');
        if (stage && !targetParent.contains(stage)) {
          targetParent.appendChild(stage);
        }
      };

      document.addEventListener('fullscreenchange', syncOverlayWithFullscreen);
      document.addEventListener('webkitfullscreenchange', syncOverlayWithFullscreen);
    }

    initKeyboardShortcuts() {
      document.addEventListener('keydown', (e) => {
        const activeEl = document.activeElement;
        if (activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA' || activeEl.isContentEditable)) return;
        if (e.key === 'm' || e.key === 'M') {
          if (this.roomId) {
            e.preventDefault();
            this.toggleMic();
          }
        } else if (e.key === 'v' || e.key === 'V') {
          if (this.roomId) {
            e.preventDefault();
            this.toggleCam();
          }
        }
      });
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

        // Listen for incoming Voice / Couple Cam Media Calls
        this.peer.on('call', (mediaCall) => {
          this.setupMediaCall(mediaCall);
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

        // If local mic or cam is already active, call peer with media
        if (this.localStream) {
          setTimeout(() => this.callPeerWithMedia(conn.peer), 600);
        }
      });

      conn.on('data', (data) => {
        this.handleMessage(data);
      });

      conn.on('close', () => {
        this.peerConnections.delete(conn.peer);
        this.remoteStreams.delete(conn.peer);
        this.mediaCalls.delete(conn.peer);
        this.updateCoupleCamDock();
      });
    }

    createPlaceholderMediaStream() {
      try {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) return new MediaStream();
        const ctx = new AudioContextClass();
        const osc = ctx.createOscillator();
        const dst = ctx.createMediaStreamDestination();
        osc.connect(dst);
        osc.start();
        const track = dst.stream.getAudioTracks()[0];
        if (track) track.enabled = false;
        return dst.stream;
      } catch (e) {
        return new MediaStream();
      }
    }

    setupMediaCall(mediaCall) {
      this.mediaCalls.set(mediaCall.peer, mediaCall);
      const streamToAnswer = this.localStream || this.createPlaceholderMediaStream();
      mediaCall.answer(streamToAnswer);

      mediaCall.on('stream', (remoteStream) => {
        this.remoteStreams.set(mediaCall.peer, remoteStream);
        this.handleRemoteStream(mediaCall.peer, remoteStream);
      });

      mediaCall.on('close', () => {
        this.remoteStreams.delete(mediaCall.peer);
        this.mediaCalls.delete(mediaCall.peer);
        this.updateCoupleCamDock();
      });

      mediaCall.on('error', (err) => {
        console.warn('MediaCall note:', err);
      });
    }

    callPeerWithMedia(peerId) {
      if (!this.peer || !this.localStream) return;
      const existingCall = this.mediaCalls.get(peerId);
      if (existingCall && existingCall.open) {
        try {
          const senders = existingCall.peerConnection.getSenders();
          const vTrack = this.localStream.getVideoTracks()[0] || null;
          const aTrack = this.localStream.getAudioTracks()[0] || null;
          senders.forEach(s => {
            if (s.track && s.track.kind === 'video' && vTrack) s.replaceTrack(vTrack);
            if (s.track && s.track.kind === 'audio' && aTrack) s.replaceTrack(aTrack);
          });
          return;
        } catch (e) {}
      }

      try {
        const call = this.peer.call(peerId, this.localStream);
        if (call) {
          this.mediaCalls.set(peerId, call);
          call.on('stream', (remoteStream) => {
            this.remoteStreams.set(peerId, remoteStream);
            this.handleRemoteStream(peerId, remoteStream);
          });
          call.on('close', () => {
            this.remoteStreams.delete(peerId);
            this.mediaCalls.delete(peerId);
            this.updateCoupleCamDock();
          });
        }
      } catch (err) {
        console.warn('Call peer error:', err);
      }
    }

    callAllPeersWithMedia() {
      if (!this.peer || !this.localStream) return;
      for (const peerId of this.peerConnections.keys()) {
        this.callPeerWithMedia(peerId);
      }
    }

    handleRemoteStream(peerId, remoteStream) {
      let audioEl = document.getElementById(`wp-remote-audio-${peerId}`);
      if (!audioEl) {
        audioEl = document.createElement('audio');
        audioEl.id = `wp-remote-audio-${peerId}`;
        audioEl.autoplay = true;
        audioEl.playsInline = true;
        document.body.appendChild(audioEl);
      }
      audioEl.srcObject = remoteStream;
      audioEl.play().catch(() => {});

      this.setupRemoteAudioAnalyser(peerId, remoteStream);
      this.updateCoupleCamDock();
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

        case 'MEDIA_STATUS':
          if (data.userId) {
            this.remoteMediaStatus.set(data.userId, {
              isMicOn: !!data.isMicOn,
              isCamOn: !!data.isCamOn,
              userName: data.userName || 'Partner'
            });
            this.updateCoupleCamDock();
            if (data.isCamOn) {
              const uName = data.userName || 'Partner';
              this.showActionToast(`${uName} turned ON their camera 👫📷`);
            }
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
          <!-- Row 1: Title, Dual Control Mode Pill & Close Button -->
          <div class="wp-header-top">
            <div class="wp-title">
              <span class="wp-title-beacon"></span>
              <i class="fa-solid fa-crown" style="color:#f59e0b"></i>
              <span class="wp-vip-title">WATCH WITH FRIENDS</span>
            </div>
            <div class="wp-header-top-actions">
              ${this.isHost ? `
                <button type="button" class="wp-lock-toggle ${this.isLocked ? 'locked' : ''}" id="wp-lock-toggle" title="Click to toggle control mode">
                  <i class="fa-solid ${this.isLocked ? 'fa-lock' : 'fa-lock-open'}"></i>
                  <span id="wp-lock-label">${this.isLocked ? 'Host Only' : 'Everyone'}</span>
                </button>
              ` : `
                <span class="wp-lock-toggle ${this.isLocked ? 'locked' : ''}" style="cursor:default">
                  <i class="fa-solid ${this.isLocked ? 'fa-lock' : 'fa-lock-open'}"></i>
                  <span>${this.isLocked ? 'Host Only' : 'Everyone'}</span>
                </span>
              `}
              <button type="button" class="wp-close-btn" id="wp-close-btn" title="Minimize Lounge" aria-label="Close Lounge">
                <i class="fa-solid fa-xmark"></i>
              </button>
            </div>
          </div>

          <!-- Row 2: Room Code Badge & Action Cluster (Popups, Mic, Cam) -->
          <div class="wp-header-toolbar">
            <button type="button" class="wp-room-badge" id="wp-room-code" title="Click to copy room invitation link">
              <i class="fa-solid fa-link"></i>
              <span id="wp-code-text">${this.roomId}</span>
              <span class="wp-copy-pill"><i class="fa-regular fa-copy"></i> Copy</span>
            </button>

            <div class="wp-toolbar-actions">
              <!-- In-Video Chat Popups Toggle -->
              <button type="button" class="wp-popups-toggle ${this.chatPopupsEnabled !== false ? 'active' : ''}" id="wp-popups-toggle" title="Toggle On-Screen Chat Popups on Video">
                <i class="fa-solid ${this.chatPopupsEnabled !== false ? 'fa-comment-dots' : 'fa-comment-slash'}"></i>
                <span id="wp-popups-label">${this.chatPopupsEnabled !== false ? 'Popups ON' : 'Popups OFF'}</span>
              </button>
              <!-- Voice Chat Mic Toggle -->
              <button type="button" class="wp-media-btn ${this.isMicOn ? 'active' : ''}" id="wp-btn-mic" title="Toggle Voice Chat Mic (M)">
                <i class="fa-solid ${this.isMicOn ? 'fa-microphone' : 'fa-microphone-slash'}"></i>
              </button>
              <!-- Couple Cam PiP Toggle -->
              <button type="button" class="wp-media-btn ${this.isCamOn ? 'active' : ''}" id="wp-btn-cam" title="Toggle Couple Cam PiP (V)">
                <i class="fa-solid ${this.isCamOn ? 'fa-video' : 'fa-video-slash'}"></i>
              </button>
            </div>
          </div>
        </div>

        <div class="wp-members-bar" id="wp-members-bar">
          <!-- Populated by updateMembersList -->
        </div>

        <div class="wp-body">
          <div class="wp-chat-box" id="wp-chat-box">
            <!-- Pure live chat messages stream -->
          </div>

          <!-- 3D Fluent Live Reactions Deck (12 Modern Expressive Reactions) -->
          <div class="wp-floating-reactions-bar">
            <button type="button" class="wp-reaction-chip" data-emoji="fire" title="Fire 🔥">
              <img src="${WP_3D_EMOJIS.fire}" alt="Fire" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="popcorn" title="Popcorn 🍿">
              <img src="${WP_3D_EMOJIS.popcorn}" alt="Popcorn" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="heart" title="Love ❤️">
              <img src="${WP_3D_EMOJIS.heart}" alt="Heart" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="mindblown" title="Mind Blown 🤯">
              <img src="${WP_3D_EMOJIS.mindblown}" alt="Mind Blown" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="laugh" title="Laugh 🤣">
              <img src="${WP_3D_EMOJIS.laugh}" alt="Laugh" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="party" title="Party 🎉">
              <img src="${WP_3D_EMOJIS.party}" alt="Party" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="shock" title="Shock 😱">
              <img src="${WP_3D_EMOJIS.shock}" alt="Shock" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="clap" title="Clap 👏">
              <img src="${WP_3D_EMOJIS.clap}" alt="Clap" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="bolt" title="Hype ⚡">
              <img src="${WP_3D_EMOJIS.bolt}" alt="Hype" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="skull" title="Dead 💀">
              <img src="${WP_3D_EMOJIS.skull}" alt="Dead" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="crown" title="GOAT 👑">
              <img src="${WP_3D_EMOJIS.crown}" alt="Crown" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="star" title="Star-struck 🤩">
              <img src="${WP_3D_EMOJIS.star}" alt="Star" class="react-3d-emoji" loading="lazy">
            </button>
          </div>

          <!-- WhatsApp-Style Chat Input Bar with Emoji & Cinema Sticker Picker -->
          <div class="wp-chat-input-wrapper">
            <!-- WhatsApp-Style Emoji & Cinema Sticker Popover -->
            <div class="wp-picker-popover" id="wp-picker-popover" style="display:none">
              <div class="wp-picker-header">
                <div class="wp-picker-tabs">
                  <button type="button" class="wp-picker-tab-btn active" data-tab="emojis"><i class="fa-regular fa-face-smile"></i> Emojis</button>
                  <button type="button" class="wp-picker-tab-btn" data-tab="stickers"><i class="fa-solid fa-film"></i> Cinema Stickers</button>
                </div>
                <button type="button" class="wp-picker-close-btn" id="wp-picker-close-btn" title="Close Picker"><i class="fa-solid fa-xmark"></i></button>
              </div>
              <div class="wp-picker-body" id="wp-picker-body">
                <!-- Injected dynamically by initEmojiAndStickerPicker -->
              </div>
            </div>

            <form class="wp-chat-input-bar" id="wp-chat-form">
              <button type="button" class="wp-btn-smiley" id="wp-btn-smiley" title="Insert Emoji or Cinema Sticker (WhatsApp Style)" aria-label="Open Emoji Picker">
                <i class="fa-regular fa-face-smile"></i>
              </button>
              <input type="text" class="wp-chat-input" id="wp-chat-input" placeholder="Type a message or send stickers..." autocomplete="off">
              <button type="submit" class="wp-btn-send" id="wp-btn-send" aria-label="Send message">
                <i class="fa-solid fa-paper-plane"></i>
              </button>
            </form>
          </div>
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

      // Wire Room Code Copy with instant visual feedback
      const roomBadge = container.querySelector('#wp-room-code');
      if (roomBadge) {
        roomBadge.addEventListener('click', () => {
          this.shareInviteLink();
          const codeText = roomBadge.querySelector('#wp-code-text');
          const originalText = this.roomId;
          if (codeText) {
            codeText.textContent = 'Copied! ✓';
            roomBadge.classList.add('copied');
            setTimeout(() => {
              codeText.textContent = originalText;
              roomBadge.classList.remove('copied');
            }, 2000);
          }
        });
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

      // Wire Popups Toggle
      const popupsToggle = container.querySelector('#wp-popups-toggle');
      if (popupsToggle) {
        popupsToggle.addEventListener('click', () => this.toggleChatPopups());
      }

      // Wire Player Menu Bar Popups Toggle button if present
      const barToggle = document.getElementById('btn-toggle-chat-popups');
      if (barToggle && !barToggle._bound) {
        barToggle._bound = true;
        barToggle.addEventListener('click', () => this.toggleChatPopups());
      }
      this.updateChatPopupsUI();

      // Wire Mic buttons in Lounge & Player Menu Bar
      const wpMicBtn = container.querySelector('#wp-btn-mic');
      if (wpMicBtn) {
        wpMicBtn.addEventListener('click', () => this.toggleMic());
      }
      const barMicBtn = document.getElementById('btn-bar-mic');
      if (barMicBtn && !barMicBtn._bound) {
        barMicBtn._bound = true;
        barMicBtn.addEventListener('click', () => this.toggleMic());
      }

      // Wire Cam buttons in Lounge & Player Menu Bar
      const wpCamBtn = container.querySelector('#wp-btn-cam');
      if (wpCamBtn) {
        wpCamBtn.addEventListener('click', () => this.toggleCam());
      }
      const barCamBtn = document.getElementById('btn-bar-cam');
      if (barCamBtn && !barCamBtn._bound) {
        barCamBtn._bound = true;
        barCamBtn.addEventListener('click', () => this.toggleCam());
      }
      this.updateMediaControlsUI();

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

      // Wire WhatsApp-Style Emoji & Cinema Sticker Picker
      this.initEmojiAndStickerPicker(container);
    }

    initEmojiAndStickerPicker(container) {
      const smileyBtn = container.querySelector('#wp-btn-smiley');
      const popover = container.querySelector('#wp-picker-popover');
      const closeBtn = container.querySelector('#wp-picker-close-btn');
      const body = container.querySelector('#wp-picker-body');
      const tabBtns = container.querySelectorAll('.wp-picker-tab-btn');
      const input = container.querySelector('#wp-chat-input');

      if (!smileyBtn || !popover || !body) return;

      let activeTab = 'emojis';

      const renderContent = () => {
        if (activeTab === 'emojis') {
          body.innerHTML = `
            <div class="wp-picker-emojis-grid">
              ${WP_QUICK_EMOJIS.map(e => {
                const url = window.FilmZillaEmoji ? window.FilmZillaEmoji.getEmojiUrl(e) : null;
                const inner = url
                  ? `<img src="${url}" alt="${e}" class="wp-picker-emoji-img" loading="lazy">`
                  : e;
                return `<button type="button" class="wp-emoji-btn" data-emoji="${e}" title="${e}">${inner}</button>`;
              }).join('')}
            </div>
          `;
          body.querySelectorAll('.wp-emoji-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
              e.preventDefault();
              e.stopPropagation();
              const emoji = btn.dataset.emoji;
              if (input && emoji) {
                input.value += emoji;
                input.focus();
              }
            });
          });
        } else {
          body.innerHTML = `
            <div class="wp-picker-stickers-grid">
              ${WP_CINEMA_STICKERS.map(s => `
                <button type="button" class="wp-sticker-btn" data-sticker-key="${s.key}" title="${s.name}">
                  <img src="${s.img}" alt="${s.name}" class="wp-sticker-thumb" loading="lazy">
                  <span class="wp-sticker-caption">${s.name}</span>
                </button>
              `).join('')}
            </div>
          `;
          body.querySelectorAll('.wp-sticker-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
              e.preventDefault();
              e.stopPropagation();
              const key = btn.dataset.stickerKey;
              const sticker = WP_CINEMA_STICKERS.find(s => s.key === key);
              if (sticker) {
                this.sendSticker(sticker);
              }
            });
          });
        }
      };

      smileyBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const isShowing = popover.style.display !== 'none';
        popover.style.display = isShowing ? 'none' : 'flex';
        if (!isShowing) renderContent();
      });

      if (closeBtn) {
        closeBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          popover.style.display = 'none';
        });
      }

      tabBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          tabBtns.forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          activeTab = btn.dataset.tab;
          renderContent();
        });
      });

      document.addEventListener('click', (e) => {
        if (!popover.contains(e.target) && e.target !== smileyBtn && !smileyBtn.contains(e.target)) {
          popover.style.display = 'none';
        }
      });
    }

    sendSticker(sticker) {
      if (!sticker || !this.roomId) return;
      const stickerPayload = `[STICKER:${sticker.name}:${sticker.img}]`;
      this.appendChatMessage(this.localUser.name, stickerPayload, true);
      this.broadcast({
        type: 'CHAT_MESSAGE',
        userName: this.localUser.name,
        text: stickerPayload,
        timestamp: Date.now()
      });
      if (typeof window.spawnFloatingEmoji === 'function') {
        window.spawnFloatingEmoji(null, sticker.key || 'star');
      }
      const popover = document.getElementById('wp-picker-popover');
      if (popover) popover.style.display = 'none';
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

    toggleChatPopups(forcedState) {
      if (typeof forcedState === 'boolean') {
        this.chatPopupsEnabled = forcedState;
      } else {
        this.chatPopupsEnabled = !this.chatPopupsEnabled;
      }
      try {
        localStorage.setItem('fz_chat_popups_enabled', this.chatPopupsEnabled ? 'true' : 'false');
      } catch (e) {}

      this.updateChatPopupsUI();

      if (!this.chatPopupsEnabled) {
        const overlay = document.getElementById('cs-video-chat-overlay');
        if (overlay) overlay.innerHTML = '';
        this.showActionToast('In-screen chat popups turned OFF 🔕');
      } else {
        this.showActionToast('In-screen chat popups turned ON 🔔');
      }
    }

    updateChatPopupsUI() {
      const enabled = this.chatPopupsEnabled !== false;
      const wpToggle = document.getElementById('wp-popups-toggle');
      if (wpToggle) {
        wpToggle.classList.toggle('active', enabled);
        const icon = wpToggle.querySelector('i');
        if (icon) icon.className = `fa-solid ${enabled ? 'fa-comment-dots' : 'fa-comment-slash'}`;
        const lbl = wpToggle.querySelector('#wp-popups-label');
        if (lbl) lbl.textContent = enabled ? 'Popups ON' : 'Popups OFF';
      }

      const barToggle = document.getElementById('btn-toggle-chat-popups');
      if (barToggle) {
        barToggle.classList.toggle('active', enabled);
        const icon = barToggle.querySelector('i');
        if (icon) icon.className = `fa-solid ${enabled ? 'fa-comment-dots' : 'fa-comment-slash'}`;
        const badge = barToggle.querySelector('#cs-popups-badge');
        if (badge) badge.textContent = enabled ? 'ON' : 'OFF';
      }
    }

    syncLocalTracksToPeers() {
      if (!this.peer || !this.localStream) return;

      const vTrack = this.localStream.getVideoTracks()[0] || null;
      const aTrack = this.localStream.getAudioTracks()[0] || null;

      for (const [peerId, call] of this.mediaCalls.entries()) {
        if (call && call.peerConnection) {
          try {
            const senders = call.peerConnection.getSenders();
            let vReplaced = false;
            let aReplaced = false;

            senders.forEach(s => {
              if (s.track && s.track.kind === 'video') {
                s.replaceTrack(vTrack);
                vReplaced = true;
              } else if (s.track && s.track.kind === 'audio') {
                s.replaceTrack(aTrack);
                aReplaced = true;
              }
            });

            if (!vReplaced && vTrack) {
              call.peerConnection.addTrack(vTrack, this.localStream);
            }
            if (!aReplaced && aTrack) {
              call.peerConnection.addTrack(aTrack, this.localStream);
            }
          } catch (e) {
            console.warn('syncLocalTracks note:', e);
          }
        }
      }

      for (const peerId of this.peerConnections.keys()) {
        if (!this.mediaCalls.has(peerId)) {
          this.callPeerWithMedia(peerId);
        }
      }
    }

    broadcastMediaStatus() {
      this.broadcast({
        type: 'MEDIA_STATUS',
        userId: this.localUser.id,
        userName: this.localUser.name,
        isMicOn: this.isMicOn,
        isCamOn: this.isCamOn,
        timestamp: Date.now()
      });
    }

    async toggleMic() {
      if (!this.roomId) {
        this.showActionToast('Start or join a Watch Party room first 👥');
        return;
      }

      try {
        if (this.isMicOn) {
          this.isMicOn = false;
          if (this.localStream) {
            this.localStream.getAudioTracks().forEach(t => {
              t.enabled = false;
              t.stop();
              this.localStream.removeTrack(t);
            });
          }
          if (this.localAudioInterval) {
            clearInterval(this.localAudioInterval);
            this.localAudioInterval = null;
          }
          const localBubble = document.querySelector('.cs-cam-bubble[data-user="local"]');
          if (localBubble) localBubble.classList.remove('is-speaking');

          this.syncLocalTracksToPeers();
          this.broadcastMediaStatus();
          this.updateMediaControlsUI();
          this.updateCoupleCamDock();
          this.showActionToast('Microphone Muted 🔇');
        } else {
          let audioStream;
          try {
            audioStream = await navigator.mediaDevices.getUserMedia({
              audio: {
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true
              }
            });
          } catch (err) {
            console.warn('Microphone permission denied/error:', err);
            this.showActionToast('Please allow Microphone access to speak 🎙️');
            return;
          }

          const aTrack = audioStream.getAudioTracks()[0];
          if (!aTrack) {
            this.showActionToast('No microphone found on your device');
            return;
          }

          if (!this.localStream) {
            this.localStream = new MediaStream();
          }
          this.localStream.getAudioTracks().forEach(t => {
            t.stop();
            this.localStream.removeTrack(t);
          });
          this.localStream.addTrack(aTrack);
          this.isMicOn = true;

          this.syncLocalTracksToPeers();
          this.setupLocalAudioAnalyser();
          this.broadcastMediaStatus();
          this.updateMediaControlsUI();
          this.updateCoupleCamDock();
          this.showActionToast('Microphone ON 🎙️ Voice chat active');
        }
      } catch (err) {
        console.error('toggleMic error:', err);
        this.showActionToast('Could not access microphone');
      }
    }

    async toggleCam() {
      if (!this.roomId) {
        this.showActionToast('Start or join a Watch Party room first 👥');
        return;
      }

      try {
        if (this.isCamOn) {
          this.isCamOn = false;
          if (this.localStream) {
            this.localStream.getVideoTracks().forEach(t => {
              t.stop();
              this.localStream.removeTrack(t);
            });
          }
          this.syncLocalTracksToPeers();
          this.broadcastMediaStatus();
          this.updateMediaControlsUI();
          this.updateCoupleCamDock();
          this.showActionToast('Camera turned OFF 📷');
        } else {
          let videoStream;
          try {
            videoStream = await navigator.mediaDevices.getUserMedia({
              video: {
                width: { ideal: 320 },
                height: { ideal: 320 },
                facingMode: 'user'
              }
            });
          } catch (err) {
            console.warn('Camera permission denied/error:', err);
            this.showActionToast('Please allow Camera access for Couple Cam 👫📷');
            return;
          }

          const vTrack = videoStream.getVideoTracks()[0];
          if (!vTrack) {
            this.showActionToast('No camera found on your device');
            return;
          }

          if (!this.localStream) {
            this.localStream = new MediaStream();
          }
          this.localStream.getVideoTracks().forEach(t => {
            t.stop();
            this.localStream.removeTrack(t);
          });
          this.localStream.addTrack(vTrack);
          this.isCamOn = true;

          this.syncLocalTracksToPeers();
          this.broadcastMediaStatus();
          this.updateMediaControlsUI();
          this.updateCoupleCamDock();
          this.showActionToast('Couple Cam ON 👫 High-Definition PiP');
        }
      } catch (err) {
        console.error('toggleCam error:', err);
        this.showActionToast('Could not access camera');
      }
    }

    updateMediaControlsUI() {
      const wpMic = document.getElementById('wp-btn-mic');
      if (wpMic) {
        wpMic.classList.toggle('active', this.isMicOn);
        const icon = wpMic.querySelector('i');
        if (icon) icon.className = `fa-solid ${this.isMicOn ? 'fa-microphone' : 'fa-microphone-slash'}`;
      }
      const barMic = document.getElementById('btn-bar-mic');
      if (barMic) {
        barMic.classList.toggle('active', this.isMicOn);
        const icon = barMic.querySelector('i');
        if (icon) icon.className = `fa-solid ${this.isMicOn ? 'fa-microphone' : 'fa-microphone-slash'}`;
        const badge = barMic.querySelector('#cs-mic-badge');
        if (badge) badge.textContent = this.isMicOn ? 'ON' : 'OFF';
      }

      const wpCam = document.getElementById('wp-btn-cam');
      if (wpCam) {
        wpCam.classList.toggle('active', this.isCamOn);
        const icon = wpCam.querySelector('i');
        if (icon) icon.className = `fa-solid ${this.isCamOn ? 'fa-video' : 'fa-video-slash'}`;
      }
      const barCam = document.getElementById('btn-bar-cam');
      if (barCam) {
        barCam.classList.toggle('active', this.isCamOn);
        const icon = barCam.querySelector('i');
        if (icon) icon.className = `fa-solid ${this.isCamOn ? 'fa-video' : 'fa-video-slash'}`;
        const badge = barCam.querySelector('#cs-cam-badge');
        if (badge) badge.textContent = this.isCamOn ? 'ON' : 'OFF';
      }
    }

    updateCoupleCamDock() {
      const fsElem = document.fullscreenElement || document.webkitFullscreenElement;
      const targetParent = fsElem || document.getElementById('filmsubPlayer') || document.getElementById('video-player-container') || document.querySelector('.player-container');
      if (!targetParent) return;

      let dock = document.getElementById('cs-couple-cam-dock');
      if (!dock) {
        dock = document.createElement('div');
        dock.id = 'cs-couple-cam-dock';
        dock.className = 'cs-couple-cam-dock';
        dock.setAttribute('aria-label', 'Couple Video Cam PiP');
        targetParent.appendChild(dock);
      } else if (!targetParent.contains(dock)) {
        targetParent.appendChild(dock);
      }

      const hasLocalActive = this.isCamOn || this.isMicOn;
      let hasRemoteActive = false;
      for (const [userId, st] of this.remoteMediaStatus.entries()) {
        if (st && (st.isCamOn || st.isMicOn)) {
          hasRemoteActive = true;
          break;
        }
      }
      for (const [peerId, stream] of this.remoteStreams.entries()) {
        if (stream && (stream.getVideoTracks().length > 0 || stream.getAudioTracks().length > 0)) {
          hasRemoteActive = true;
          break;
        }
      }

      if (!hasLocalActive && !hasRemoteActive) {
        dock.style.display = 'none';
        return;
      }

      dock.style.display = 'flex';

      // Render Dock Shell if empty
      let header = dock.querySelector('#cs-cam-dock-drag-bar');
      if (!header) {
        dock.innerHTML = `
          <div class="cs-cam-dock-header" id="cs-cam-dock-drag-bar" title="Drag to reposition anywhere on screen">
            <div class="cs-cam-dock-title">
              <span class="cs-cam-live-dot"></span>
              <span class="cs-cam-title-text">COUPLE CAM</span>
            </div>
            <div class="cs-cam-dock-actions">
              <button type="button" class="cs-cam-btn-min" id="cs-cam-btn-min" title="Minimize / Expand PiP">
                <i class="fa-solid fa-minus"></i>
              </button>
              <button type="button" class="cs-cam-btn-close" id="cs-cam-btn-close" title="Close Couple Cam">
                <i class="fa-solid fa-xmark"></i>
              </button>
            </div>
          </div>
          <div class="cs-cam-bubbles-wrap" id="cs-cam-bubbles-wrap"></div>
        `;

        header = dock.querySelector('#cs-cam-dock-drag-bar');
        this.makeElementDraggable(dock, header);

        const minBtn = dock.querySelector('#cs-cam-btn-min');
        if (minBtn) {
          minBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.isCoupleCamCollapsed = !this.isCoupleCamCollapsed;
            dock.classList.toggle('is-collapsed', this.isCoupleCamCollapsed);
            const icon = minBtn.querySelector('i');
            if (icon) icon.className = `fa-solid ${this.isCoupleCamCollapsed ? 'fa-plus' : 'fa-minus'}`;
          });
        }

        const closeBtn = dock.querySelector('#cs-cam-btn-close');
        if (closeBtn) {
          closeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (this.isCamOn) this.toggleCam();
            if (this.isMicOn) this.toggleMic();
            dock.style.display = 'none';
          });
        }
      }

      const bubblesWrap = dock.querySelector('#cs-cam-bubbles-wrap');
      if (!bubblesWrap) return;

      // 1. Local Bubble
      let localBubble = bubblesWrap.querySelector('.cs-cam-bubble[data-user="local"]');
      if (hasLocalActive) {
        if (!localBubble) {
          localBubble = document.createElement('div');
          localBubble.className = 'cs-cam-bubble';
          localBubble.setAttribute('data-user', 'local');
          bubblesWrap.appendChild(localBubble);
        }

        const userInitial = (this.localUser.name || 'U').charAt(0).toUpperCase();
        if (this.isCamOn && this.localStream && this.localStream.getVideoTracks().length > 0) {
          let videoEl = localBubble.querySelector('video');
          if (!videoEl) {
            localBubble.innerHTML = `
              <video id="wp-local-cam-video" autoplay playsinline muted></video>
              <div class="cs-cam-info-badge">
                <span class="cs-cam-badge-name">You</span>
                <i class="fa-solid ${this.isMicOn ? 'fa-microphone' : 'fa-microphone-slash'}"></i>
              </div>
              <div class="cs-speaking-pulse"></div>
            `;
            videoEl = localBubble.querySelector('video');
          }
          if (videoEl && videoEl.srcObject !== this.localStream) {
            videoEl.srcObject = this.localStream;
            videoEl.play().catch(() => {});
          }
          const micIcon = localBubble.querySelector('.cs-cam-info-badge i');
          if (micIcon) micIcon.className = `fa-solid ${this.isMicOn ? 'fa-microphone' : 'fa-microphone-slash'}`;
        } else {
          // Audio only mode
          localBubble.innerHTML = `
            <div class="cs-cam-avatar-placeholder">
              <span>${this.escapeHtml(userInitial)}</span>
            </div>
            <div class="cs-cam-info-badge">
              <span class="cs-cam-badge-name">You</span>
              <i class="fa-solid ${this.isMicOn ? 'fa-microphone' : 'fa-microphone-slash'}"></i>
            </div>
            <div class="cs-speaking-pulse"></div>
          `;
        }
      } else if (localBubble) {
        localBubble.remove();
      }

      // 2. Remote Partner Bubbles
      const processedPeers = new Set();
      for (const [peerId, remoteStream] of this.remoteStreams.entries()) {
        processedPeers.add(peerId);
        let remoteBubble = bubblesWrap.querySelector(`.cs-cam-bubble[data-user="${peerId}"]`);
        if (!remoteBubble) {
          remoteBubble = document.createElement('div');
          remoteBubble.className = 'cs-cam-bubble';
          remoteBubble.setAttribute('data-user', peerId);
          bubblesWrap.appendChild(remoteBubble);
        }

        const hasVideo = remoteStream.getVideoTracks().length > 0;
        let pName = 'Partner';
        let pMic = true;
        for (const [uid, st] of this.remoteMediaStatus.entries()) {
          if (st && st.userName) {
            pName = st.userName;
            pMic = !!st.isMicOn;
            break;
          }
        }
        const pInitial = pName.charAt(0).toUpperCase();

        if (hasVideo) {
          let videoEl = remoteBubble.querySelector('video');
          if (!videoEl) {
            remoteBubble.innerHTML = `
              <video id="wp-remote-cam-${peerId}" autoplay playsinline></video>
              <div class="cs-cam-info-badge">
                <span class="cs-cam-badge-name">${this.escapeHtml(pName)}</span>
                <i class="fa-solid ${pMic ? 'fa-microphone' : 'fa-microphone-slash'}"></i>
              </div>
              <div class="cs-speaking-pulse"></div>
            `;
            videoEl = remoteBubble.querySelector('video');
          }
          if (videoEl && videoEl.srcObject !== remoteStream) {
            videoEl.srcObject = remoteStream;
            videoEl.play().catch(() => {});
          }
          const micIcon = remoteBubble.querySelector('.cs-cam-info-badge i');
          if (micIcon) micIcon.className = `fa-solid ${pMic ? 'fa-microphone' : 'fa-microphone-slash'}`;
        } else {
          remoteBubble.innerHTML = `
            <div class="cs-cam-avatar-placeholder">
              <span>${this.escapeHtml(pInitial)}</span>
            </div>
            <div class="cs-cam-info-badge">
              <span class="cs-cam-badge-name">${this.escapeHtml(pName)}</span>
              <i class="fa-solid ${pMic ? 'fa-microphone' : 'fa-microphone-slash'}"></i>
            </div>
            <div class="cs-speaking-pulse"></div>
          `;
        }
      }

      // Remove orphaned bubbles
      bubblesWrap.querySelectorAll('.cs-cam-bubble:not([data-user="local"])').forEach(b => {
        const u = b.getAttribute('data-user');
        if (u && !processedPeers.has(u)) {
          b.remove();
        }
      });
    }

    makeElementDraggable(dock, dragBar) {
      if (!dock || !dragBar || dock._isDraggableInitialized) return;
      dock._isDraggableInitialized = true;

      let isDragging = false;
      let startX, startY, initialLeft, initialTop;

      const onMouseDown = (e) => {
        if (e.target.closest('button')) return;
        isDragging = true;
        startX = e.clientX;
        startY = e.clientY;
        const rect = dock.getBoundingClientRect();
        const parentRect = dock.parentElement.getBoundingClientRect();
        initialLeft = rect.left - parentRect.left;
        initialTop = rect.top - parentRect.top;
        dock.style.right = 'auto';
        dock.style.bottom = 'auto';
        dock.style.left = `${initialLeft}px`;
        dock.style.top = `${initialTop}px`;
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
        e.preventDefault();
      };

      const onMouseMove = (e) => {
        if (!isDragging) return;
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        const parentRect = dock.parentElement.getBoundingClientRect();
        const dockRect = dock.getBoundingClientRect();

        let newLeft = initialLeft + dx;
        let newTop = initialTop + dy;

        newLeft = Math.max(8, Math.min(newLeft, parentRect.width - dockRect.width - 8));
        newTop = Math.max(8, Math.min(newTop, parentRect.height - dockRect.height - 8));

        dock.style.left = `${newLeft}px`;
        dock.style.top = `${newTop}px`;
      };

      const onMouseUp = () => {
        isDragging = false;
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
      };

      const onTouchStart = (e) => {
        if (e.target.closest('button')) return;
        if (e.touches.length !== 1) return;
        isDragging = true;
        startX = e.touches[0].clientX;
        startY = e.touches[0].clientY;
        const rect = dock.getBoundingClientRect();
        const parentRect = dock.parentElement.getBoundingClientRect();
        initialLeft = rect.left - parentRect.left;
        initialTop = rect.top - parentRect.top;
        dock.style.right = 'auto';
        dock.style.bottom = 'auto';
        dock.style.left = `${initialLeft}px`;
        dock.style.top = `${initialTop}px`;
      };

      const onTouchMove = (e) => {
        if (!isDragging || e.touches.length !== 1) return;
        const dx = e.touches[0].clientX - startX;
        const dy = e.touches[0].clientY - startY;
        const parentRect = dock.parentElement.getBoundingClientRect();
        const dockRect = dock.getBoundingClientRect();

        let newLeft = initialLeft + dx;
        let newTop = initialTop + dy;

        newLeft = Math.max(8, Math.min(newLeft, parentRect.width - dockRect.width - 8));
        newTop = Math.max(8, Math.min(newTop, parentRect.height - dockRect.height - 8));

        dock.style.left = `${newLeft}px`;
        dock.style.top = `${newTop}px`;
      };

      const onTouchEnd = () => {
        isDragging = false;
      };

      dragBar.addEventListener('mousedown', onMouseDown);
      dragBar.addEventListener('touchstart', onTouchStart, { passive: true });
      dragBar.addEventListener('touchmove', onTouchMove, { passive: true });
      dragBar.addEventListener('touchend', onTouchEnd);
    }

    setupLocalAudioAnalyser() {
      if (!this.localStream || !this.isMicOn) return;
      try {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) return;
        if (!this.audioContext) this.audioContext = new AudioContextClass();
        if (this.audioContext.state === 'suspended') this.audioContext.resume();

        const audioTrack = this.localStream.getAudioTracks()[0];
        if (!audioTrack) return;

        const sourceStream = new MediaStream([audioTrack]);
        const sourceNode = this.audioContext.createMediaStreamSource(sourceStream);
        const analyser = this.audioContext.createAnalyser();
        analyser.fftSize = 128;
        sourceNode.connect(analyser);

        const dataArray = new Uint8Array(analyser.frequencyBinCount);
        if (this.localAudioInterval) clearInterval(this.localAudioInterval);

        this.localAudioInterval = setInterval(() => {
          if (!this.isMicOn) {
            clearInterval(this.localAudioInterval);
            this.localAudioInterval = null;
            return;
          }
          analyser.getByteFrequencyData(dataArray);
          let sum = 0;
          for (let i = 0; i < dataArray.length; i++) {
            sum += dataArray[i];
          }
          const avg = sum / dataArray.length;
          const localBubble = document.querySelector('.cs-cam-bubble[data-user="local"]');
          if (localBubble) {
            localBubble.classList.toggle('is-speaking', avg > 14);
          }
        }, 120);
      } catch (e) {
        console.warn('Local Audio Analyser note:', e);
      }
    }

    setupRemoteAudioAnalyser(peerId, remoteStream) {
      if (!remoteStream || remoteStream.getAudioTracks().length === 0) return;
      try {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) return;
        if (!this.audioContext) this.audioContext = new AudioContextClass();
        if (this.audioContext.state === 'suspended') this.audioContext.resume();

        const sourceNode = this.audioContext.createMediaStreamSource(remoteStream);
        const analyser = this.audioContext.createAnalyser();
        analyser.fftSize = 128;
        sourceNode.connect(analyser);

        const dataArray = new Uint8Array(analyser.frequencyBinCount);
        const intervalId = setInterval(() => {
          if (!this.remoteStreams.has(peerId)) {
            clearInterval(intervalId);
            return;
          }
          analyser.getByteFrequencyData(dataArray);
          let sum = 0;
          for (let i = 0; i < dataArray.length; i++) {
            sum += dataArray[i];
          }
          const avg = sum / dataArray.length;
          const remoteBubble = document.querySelector(`.cs-cam-bubble[data-user="${peerId}"]`);
          if (remoteBubble) {
            remoteBubble.classList.toggle('is-speaking', avg > 14);
          }
        }, 120);
      } catch (e) {
        console.warn('Remote Audio Analyser note:', e);
      }
    }

    showInVideoChatPopup(author, text, isMe = false) {
      if (this.chatPopupsEnabled === false) return;

      const fsElem = document.fullscreenElement || document.webkitFullscreenElement;
      const parent = fsElem || document.getElementById('filmsubPlayer') || document.getElementById('video-player-container') || document.querySelector('.player-container');
      if (!parent) return;

      let overlay = parent.querySelector('#cs-video-chat-overlay');
      if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'cs-video-chat-overlay';
        overlay.className = 'cs-video-chat-overlay';
        overlay.setAttribute('aria-live', 'polite');
        parent.appendChild(overlay);
      }

      // Keep max 3 stacked popups to prevent obscuring film
      const existing = overlay.querySelectorAll('.cs-video-chat-popup:not(.is-fading)');
      if (existing.length >= 3) {
        const oldest = existing[0];
        oldest.classList.add('is-fading');
        setTimeout(() => { if (oldest.parentNode) oldest.parentNode.removeChild(oldest); }, 300);
      }

      const initial = (author || 'U').trim().charAt(0).toUpperCase();
      const popup = document.createElement('div');
      popup.className = `cs-video-chat-popup ${isMe ? 'is-me' : ''}`;

      let popupTextHtml = this.escapeHtml(text);
      if (typeof text === 'string' && text.startsWith('[STICKER:')) {
        const stripped = text.slice(9, -1);
        const colonIdx = stripped.indexOf(':');
        const stickerName = colonIdx > 0 ? stripped.slice(0, colonIdx) : 'Cinema Sticker';
        const stickerUrl = colonIdx > 0 ? stripped.slice(colonIdx + 1) : '';
        popupTextHtml = `
          <div class="vcp-sticker-pill">
            <img src="${this.escapeHtml(stickerUrl)}" alt="${this.escapeHtml(stickerName)}" class="vcp-mini-sticker" loading="lazy">
            <span>${this.escapeHtml(stickerName)}</span>
          </div>
        `;
      } else {
        const safeRaw = this.escapeHtml(text);
        const isJumbo = window.FilmZillaEmoji && window.FilmZillaEmoji.isOnlyEmojis(text);
        const parsed = window.FilmZillaEmoji ? window.FilmZillaEmoji.parse(safeRaw) : safeRaw;
        popupTextHtml = `<span class="${isJumbo ? 'jumbo-emojis' : ''}">${parsed}</span>`;
      }

      popup.innerHTML = `
        <div class="vcp-avatar">${this.escapeHtml(initial)}</div>
        <div class="vcp-content">
          <div class="vcp-header">
            <span class="vcp-author">${this.escapeHtml(author)}${isMe ? ' (You)' : ''}</span>
            <span class="vcp-tag">Live</span>
          </div>
          <div class="vcp-text">${popupTextHtml}</div>
        </div>
        <button type="button" class="vcp-close" title="Dismiss">&times;</button>
        <div class="vcp-progress-bar"></div>
      `;

      overlay.appendChild(popup);

      const closeBtn = popup.querySelector('.vcp-close');
      if (closeBtn) {
        closeBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          popup.classList.add('is-fading');
          setTimeout(() => { if (popup.parentNode) popup.parentNode.removeChild(popup); }, 300);
        });
      }

      let timer = setTimeout(() => {
        popup.classList.add('is-fading');
        setTimeout(() => { if (popup.parentNode) popup.parentNode.removeChild(popup); }, 350);
      }, 4500);

      popup.addEventListener('mouseenter', () => clearTimeout(timer));
      popup.addEventListener('mouseleave', () => {
        timer = setTimeout(() => {
          popup.classList.add('is-fading');
          setTimeout(() => { if (popup.parentNode) popup.parentNode.removeChild(popup); }, 350);
        }, 1800);
      });
    }

    appendChatMessage(author, text, isMe = false, isSystem = false) {
      const chatBox = document.getElementById('wp-chat-box');
      if (chatBox) {
        const item = document.createElement('div');
        item.className = `wp-msg-item ${isMe ? 'me' : ''}`;
        if (isSystem) {
          item.style.background = 'rgba(229, 9, 20, 0.18)';
          item.style.alignSelf = 'center';
        }

        if (typeof text === 'string' && text.startsWith('[STICKER:')) {
          const stripped = text.slice(9, -1);
          const colonIdx = stripped.indexOf(':');
          const stickerName = colonIdx > 0 ? stripped.slice(0, colonIdx) : 'Cinema Sticker';
          const stickerUrl = colonIdx > 0 ? stripped.slice(colonIdx + 1) : '';
          item.classList.add('has-sticker');
          item.innerHTML = `
            <span class="wp-msg-author">${this.escapeHtml(author)}</span>
            <div class="wp-sticker-bubble">
              <img src="${this.escapeHtml(stickerUrl)}" alt="${this.escapeHtml(stickerName)}" class="wp-sticker-img" loading="lazy">
              <span class="wp-sticker-tag"><i class="fa-brands fa-whatsapp" style="color:#25d366"></i> ${this.escapeHtml(stickerName)}</span>
            </div>
          `;
        } else {
          const safeRaw = this.escapeHtml(text);
          const isJumbo = window.FilmZillaEmoji && window.FilmZillaEmoji.isOnlyEmojis(text);
          const formattedText = window.FilmZillaEmoji ? window.FilmZillaEmoji.parse(safeRaw) : safeRaw;
          item.innerHTML = `
            <span class="wp-msg-author">${this.escapeHtml(author)}</span>
            <span class="wp-msg-text ${isJumbo ? 'jumbo-emojis' : ''}">${formattedText}</span>
          `;
        }

        chatBox.appendChild(item);
        chatBox.scrollTop = chatBox.scrollHeight;
      }

      // Show in-video floating chat popup (works in Fullscreen and regular view)
      if (!isSystem) {
        this.showInVideoChatPopup(author, text, isMe);
      }
    }

    showFloatingReaction(emoji, userName) {
      const emojiKey = String(emoji).toLowerCase();
      const emojiMap = {
        '🔥': 'fire',
        'popcorn': 'popcorn',
        '🍿': 'popcorn',
        'heart': 'heart',
        '❤️': 'heart',
        'mindblown': 'mindblown',
        '🤯': 'mindblown',
        'laugh': 'laugh',
        '😂': 'laugh',
        '🤣': 'laugh',
        'party': 'party',
        '🎉': 'party',
        'shock': 'shock',
        '😱': 'shock',
        'clap': 'clap',
        '👏': 'clap',
        'bolt': 'bolt',
        '⚡': 'bolt',
        'skull': 'skull',
        '💀': 'skull',
        'crown': 'crown',
        '👑': 'crown',
        'star': 'star',
        '🤩': 'star',
        'fire': 'fire',
        'angry': 'mindblown'
      };
      const resolvedKey = emojiMap[emojiKey] || emojiMap[emoji] || 'fire';

      if (typeof window.spawnFloatingEmoji === 'function') {
        window.spawnFloatingEmoji(null, resolvedKey);
        return;
      }

      const playerWrap = document.querySelector('.player-container') || document.querySelector('#video-player-container');
      if (!playerWrap) return;

      let stage = document.getElementById('cs-reaction-floating-stage');
      if (!stage) {
        stage = document.createElement('div');
        stage.id = 'cs-reaction-floating-stage';
        stage.className = 'cs-reaction-floating-stage';
        playerWrap.appendChild(stage);
      }

      const src = WP_3D_EMOJIS[resolvedKey] || WP_3D_EMOJIS.fire;
      const baseLeft = Math.floor(Math.random() * 60 + 20); // 20% to 80%

      const randSway = (Math.random() - 0.5) * 80;
      const randRot = (Math.random() - 0.5) * 30;

      const floater = document.createElement('div');
      floater.className = `cs-floating-hero-emoji cs-glow-${resolvedKey}`;
      floater.style.left = `${baseLeft}%`;
      floater.style.setProperty('--rand-sway', `${randSway}px`);
      floater.style.setProperty('--rand-rot', `${randRot}deg`);
      floater.innerHTML = `
        <img src="${src}" alt="${resolvedKey}" class="cs-floater-img">
        <span class="cs-floater-sparkle s1"></span>
        <span class="cs-floater-sparkle s2"></span>
      `;

      stage.appendChild(floater);
      setTimeout(() => {
        if (floater.parentNode) floater.parentNode.removeChild(floater);
      }, 2300);
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
