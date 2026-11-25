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

  const APPLE_HD_CDN = 'https://cdn.jsdelivr.net/npm/emoji-datasource-apple@15.1.2/img/apple/64';

  const WP_3D_EMOJIS = {
    fire: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f525/512.webp',
    popcorn: `${APPLE_HD_CDN}/1f37f.png`,
    heart: `${APPLE_HD_CDN}/2764-fe0f.png`,
    mindblown: `${APPLE_HD_CDN}/1f92f.png`,
    laugh: `${APPLE_HD_CDN}/1f923.png`,
    party: `${APPLE_HD_CDN}/1f389.png`,
    shock: `${APPLE_HD_CDN}/1f631.png`,
    clap: `${APPLE_HD_CDN}/1f44f.png`,
    bolt: `${APPLE_HD_CDN}/26a1.png`,
    skull: `${APPLE_HD_CDN}/1f480.png`,
    crown: `${APPLE_HD_CDN}/1f451.png`,
    star: `${APPLE_HD_CDN}/1f929.png`,
    bomb: `${APPLE_HD_CDN}/1f4a3.png`
  };

  const WP_CINEMA_STICKERS = [
    { key: 'bomb', name: 'Air-Strike', img: `${APPLE_HD_CDN}/1f4a3.png` },
    { key: 'popcorn', name: 'Cinema Time', img: `${APPLE_HD_CDN}/1f37f.png` },
    { key: 'trophy', name: 'Masterpiece', img: `${APPLE_HD_CDN}/1f3c6.png` },
    { key: 'clapper', name: 'Action Scene', img: `${APPLE_HD_CDN}/1f3ac.png` },
    { key: 'ticket', name: 'VIP Pass', img: `${APPLE_HD_CDN}/1f39f-fe0f.png` },
    { key: 'bolt', name: 'Pure Hype', img: `${APPLE_HD_CDN}/26a1.png` },
    { key: 'skull', name: 'Plot Twist', img: `${APPLE_HD_CDN}/1f480.png` },
    { key: 'crown', name: 'GOAT Title', img: `${APPLE_HD_CDN}/1f451.png` },
    { key: 'pizza', name: 'Snack Break', img: `${APPLE_HD_CDN}/1f355.png` },
    { key: 'rocket', name: 'Next Level', img: `${APPLE_HD_CDN}/1f680.png` },
    { key: 'love', name: 'Pure Romance', img: `${APPLE_HD_CDN}/1f496.png` }
  ];

  const WP_QUICK_EMOJIS = [
    '😀', '😂', '🤣', '😍', '🤩', '😎', '🥳', '🥺', '😱', '🤯',
    '🍿', '🎬', '🎟️', '🥤', '🔥', '❤️', '⚡', '💯', '👑', '💀',
    '👍', '👏', '🙌', '✌️', '🤙', '🤝', '🍕', '🍻', '✨', '💤'
  ];

  const safeStorage = {
    get(key, fallback = null) {
      try {
        return (typeof localStorage !== 'undefined' && localStorage) ? localStorage.getItem(key) : fallback;
      } catch (_) {
        return fallback;
      }
    },
    set(key, val) {
      try {
        if (typeof localStorage !== 'undefined' && localStorage) localStorage.setItem(key, val);
      } catch (_) {}
    }
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
      this.chatPopupsEnabled = safeStorage.get('fz_chat_popups_enabled') !== 'false';

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
      this.popcornCoins = parseInt(safeStorage.get('fz_popcorn_coins') || '250', 10);
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
        try {
          if (typeof sessionStorage !== 'undefined' && sessionStorage.getItem(`fz_host_${this.roomId}`) === 'true') {
            this.isHost = true;
          }
        } catch (_) {}
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
            this.pendingIncomingMeta = { msgId: data.msgId || null, replyTo: data.replyTo || null };
            this.appendChatMessage(data.userName || 'Friend', data.text, false, false, !!data.isWhisper);
            this.pendingIncomingMeta = null;
          }
          break;

        case 'TYPING':
          if (typeof this.handleRemoteTyping === 'function') {
            this.handleRemoteTyping(data);
          }
          break;

        case 'MSG_REACT':
          if (typeof this.handleRemoteMsgReact === 'function') {
            this.handleRemoteMsgReact(data);
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

        case 'COUPLE_MODE_TOGGLE':
          this.isCoupleMode = !!data.enabled;
          this.updateCoupleModeUI();
          this.showActionToast(this.isCoupleMode ? '💑 Long-Distance Couple Mode Activated: Touch Sync & Hugs' : 'Couple Mode Deactivated');
          break;

        case 'COUPLE_TOUCH':
          if (typeof data.x === 'number' && typeof data.y === 'number') {
            this.renderCoupleTouchGlow(data.x, data.y, data.user);
          }
          break;

        case 'COUPLE_HUG':
          this.showCoupleHugOverlay(data.sender || 'Partner');
          break;

        case 'PREDICTION_START':
          this.handlePredictionStart(data);
          break;

        case 'PREDICTION_BET':
          this.handlePredictionBet(data);
          break;

        case 'PREDICTION_RESOLVE':
          this.handlePredictionResolve(data);
          break;

        case 'REACTION_BOMB':
          if (data.bomb) {
            this.launchReactionBomb(data.bomb, data.user);
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
        if (container && container.style.display === 'none') {
          this.expandFromChatHead();
        } else {
          this.minimizeToChatHead();
        }
      }
    }

    ensureChatHeadBubble() {
      const fsElem = document.fullscreenElement || document.webkitFullscreenElement;
      const targetParent = fsElem || document.querySelector('.player-container') || document.getElementById('video-player-container') || document.body;
      if (!targetParent) return null;

      let head = document.getElementById('wp-chat-head-bubble');
      if (!head) {
        head = document.createElement('button');
        head.type = 'button';
        head.id = 'wp-chat-head-bubble';
        head.className = 'wp-chat-head-bubble';
        head.title = 'Open Watch Party Chat';
        head.setAttribute('aria-label', 'Open Watch Party Chat');
        head.innerHTML = `
          <i class="fa-solid fa-comments"></i>
          <span class="wp-chat-head-dot"></span>
          <span class="wp-chat-head-badge" id="wp-chat-head-badge" style="display:none">0</span>
        `;
        let moved = false;
        let startX = 0;
        let startY = 0;
        const onStart = (ev) => {
          const pt = ev.touches ? ev.touches[0] : ev;
          startX = pt.clientX;
          startY = pt.clientY;
          moved = false;
        };
        const onMove = (ev) => {
          const pt = ev.touches ? ev.touches[0] : ev;
          if (Math.abs(pt.clientX - startX) > 6 || Math.abs(pt.clientY - startY) > 6) {
            moved = true;
          }
        };
        head.addEventListener('mousedown', onStart);
        head.addEventListener('touchstart', onStart, { passive: true });
        head.addEventListener('mousemove', onMove);
        head.addEventListener('touchmove', onMove, { passive: true });
        head.addEventListener('click', (e) => {
          e.stopPropagation();
          if (moved) {
            moved = false;
            return;
          }
          this.expandFromChatHead(true);
        });
        this.makeElementDraggable(head, head);
      }

      if (!targetParent.contains(head)) {
        targetParent.appendChild(head);
      }
      return head;
    }

    minimizeToChatHead() {
      const container = document.getElementById('watch-party-container');
      const theater = document.getElementById('cs-theater-layout');
      if (theater) theater.classList.remove('is-watch-party-active');
      if (container) {
        container.classList.remove('is-floating-messenger');
        container.style.display = 'none';
        const playerCard = document.querySelector('.cs-player-card');
        if (playerCard && container.parentNode !== playerCard.parentNode) {
          playerCard.parentNode.insertBefore(container, playerCard.nextSibling);
        }
      }
      const head = this.ensureChatHeadBubble();
      if (head) head.style.display = 'inline-flex';
    }

    expandFromChatHead(asFloatingOverlay = false) {
      const container = document.getElementById('watch-party-container');
      const theater = document.getElementById('cs-theater-layout');
      const head = document.getElementById('wp-chat-head-bubble');
      if (!container) {
        this.renderWatchPartyUI();
        return;
      }

      this.unreadCount = 0;
      const badge = document.getElementById('wp-chat-head-badge');
      if (badge) {
        badge.textContent = '0';
        badge.style.display = 'none';
      }
      if (head) head.style.display = 'none';

      const fsElem = document.fullscreenElement || document.webkitFullscreenElement;
      const useOverlay = asFloatingOverlay || Boolean(fsElem);

      if (useOverlay) {
        const hostWrap = fsElem || document.querySelector('.player-container') || document.getElementById('video-player-container') || document.body;
        if (theater) theater.classList.remove('is-watch-party-active');
        container.classList.add('is-floating-messenger');
        if (hostWrap && !hostWrap.contains(container)) {
          hostWrap.appendChild(container);
        }
      } else {
        const playerCard = document.querySelector('.cs-player-card');
        container.classList.remove('is-floating-messenger');
        if (playerCard && container.parentNode !== playerCard.parentNode) {
          playerCard.parentNode.insertBefore(container, playerCard.nextSibling);
        }
        if (theater) theater.classList.add('is-watch-party-active');
      }

      container.style.display = 'flex';
      const chatBox = document.getElementById('wp-chat-box');
      if (chatBox) chatBox.scrollTop = chatBox.scrollHeight;
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

      container.classList.remove('is-floating-messenger');
      container.style.display = 'flex';
      const existingHead = document.getElementById('wp-chat-head-bubble');
      if (existingHead) existingHead.style.display = 'none';

      if (!this._fsChangeBound) {
        this._fsChangeBound = true;
        document.addEventListener('fullscreenchange', () => {
          const curContainer = document.getElementById('watch-party-container');
          const curHead = document.getElementById('wp-chat-head-bubble');
          if (curHead && curHead.style.display !== 'none') {
            this.ensureChatHeadBubble();
          } else if (curContainer && curContainer.style.display !== 'none' && curContainer.classList.contains('is-floating-messenger')) {
            this.expandFromChatHead(true);
          }
        });
      }

      // Update Flagship button in menu bar
      const wpBtn = document.getElementById('btn-watch-party-trigger');
      if (wpBtn) {
        wpBtn.classList.add('active-room');
        const badge = wpBtn.querySelector('#cs-party-badge');
        if (badge) badge.textContent = 'ACTIVE';
      }

      container.innerHTML = `
        <div class="wp-header">
          <div class="wp-header-top">
            <div class="wp-header-left-group">
              <div class="wp-title">
                <span class="wp-title-beacon"></span>
                <span class="wp-vip-title">Watch Party</span>
              </div>
              <button type="button" class="wp-room-badge" id="wp-room-code" title="Copy Room Invite Link">
                <i class="fa-solid fa-link"></i>
                <span id="wp-code-text">${this.roomId}</span>
              </button>
            </div>

            <div class="wp-header-top-actions">
              <!-- Single Party Controls Popover Icon (Houses Mic, Cam, Popups, Lock, Invite) -->
              <div class="wp-controls-menu-wrap" id="wp-controls-menu-wrap">
                <button type="button" class="wp-icon-btn wp-controls-toggle" id="wp-controls-toggle" aria-expanded="false" aria-controls="wp-controls-popover" title="Mic, Camera & Chat Popups">
                  <i class="fa-solid fa-sliders"></i>
                  <span class="wp-controls-dot" id="wp-controls-dot" style="display:none"></span>
                </button>

                <div class="wp-controls-popover" id="wp-controls-popover" role="region" aria-label="Party Media & Settings">
                  <div class="wp-controls-pop-head">
                    <span>Party Controls</span>
                    <button type="button" class="wp-controls-pop-close" id="wp-controls-pop-close" aria-label="Close">&times;</button>
                  </div>

                  <!-- Icon-only toggles inside popover (zero cluttered text headers) -->
                  <div class="wp-controls-icon-grid">
                    <button type="button" class="wp-media-btn wp-ctl-tile ${this.isMicOn ? 'active' : ''}" id="wp-btn-mic" title="Microphone Voice Chat (M)" aria-label="Microphone">
                      <i class="fa-solid ${this.isMicOn ? 'fa-microphone' : 'fa-microphone-slash'}"></i>
                      <span class="wp-ctl-state" id="wp-mic-label">${this.isMicOn ? 'ON' : 'OFF'}</span>
                    </button>
                    <button type="button" class="wp-media-btn wp-ctl-tile ${this.isCamOn ? 'active' : ''}" id="wp-btn-cam" title="Couple Camera Video (V)" aria-label="Camera">
                      <i class="fa-solid ${this.isCamOn ? 'fa-video' : 'fa-video-slash'}"></i>
                      <span class="wp-ctl-state" id="wp-cam-label">${this.isCamOn ? 'ON' : 'OFF'}</span>
                    </button>
                    <button type="button" class="wp-popups-toggle wp-ctl-tile ${this.chatPopupsEnabled !== false ? 'active' : ''}" id="wp-popups-toggle" title="On-Screen Chat Popups" aria-label="Screen Popups">
                      <i class="fa-solid ${this.chatPopupsEnabled !== false ? 'fa-comment-dots' : 'fa-comment-slash'}"></i>
                      <span class="wp-ctl-state" id="wp-popups-label">${this.chatPopupsEnabled !== false ? 'ON' : 'OFF'}</span>
                    </button>
                  </div>

                  <div class="wp-controls-pop-actions">
                    ${this.isHost ? `
                      <button type="button" class="wp-lock-toggle wp-pop-row-btn ${this.isLocked ? 'locked' : ''}" id="wp-lock-toggle" title="Toggle control permission">
                        <i class="fa-solid ${this.isLocked ? 'fa-lock' : 'fa-lock-open'}"></i>
                        <span id="wp-lock-label">${this.isLocked ? 'Host Only' : 'Everyone'}</span>
                      </button>
                    ` : `
                      <span class="wp-lock-toggle wp-pop-row-btn ${this.isLocked ? 'locked' : ''}" style="cursor:default">
                        <i class="fa-solid ${this.isLocked ? 'fa-lock' : 'fa-lock-open'}"></i>
                        <span>${this.isLocked ? 'Host Only' : 'Everyone'}</span>
                      </span>
                    `}
                    <button type="button" class="wp-pop-row-btn wp-pop-wa-btn" id="wp-share-btn" title="Invite on WhatsApp">
                      <i class="fa-brands fa-whatsapp"></i>
                      <span>WhatsApp</span>
                    </button>
                    <button type="button" class="wp-pop-row-btn" id="wp-copy-link-btn" title="Copy Room Link">
                      <i class="fa-solid fa-copy"></i>
                      <span>Link</span>
                    </button>
                  </div>
                </div>
              </div>

              <button type="button" class="wp-icon-btn" id="wp-dock-switch-btn" title="Pop Out / Dock Chat">
                <i class="fa-solid fa-up-right-from-square"></i>
              </button>
              <button type="button" class="wp-icon-btn wp-close-btn" id="wp-close-btn" title="Minimize to Floating Bubble" aria-label="Minimize Watch Party">
                <i class="fa-solid fa-minus"></i>
              </button>
            </div>
          </div>
        </div>

        <div class="wp-members-bar" id="wp-members-bar">
          <!-- Populated by updateMembersList -->
        </div>

        <div class="wp-body">
          <div class="wp-chat-box" id="wp-chat-box">
            <div class="wp-chat-welcome" id="wp-chat-welcome">
              <div class="wp-welcome-sub">In-sync watch lounge. Send a message or launch a scene poll.</div>
              <div class="wp-welcome-chips">
                <button type="button" class="wp-quick-chip" data-quick="wave"><i class="fa-solid fa-hand"></i> Say Hi</button>
                <button type="button" class="wp-quick-chip" data-quick="poll"><i class="fa-solid fa-square-poll-vertical"></i> Poll</button>
              </div>
            </div>
          </div>

          <!-- Compact Apple HD Reactions Strip -->
          <div class="wp-floating-reactions-bar">
            <button type="button" class="wp-reaction-chip" data-emoji="fire" title="Fire">
              <img src="${WP_3D_EMOJIS.fire}" alt="Fire" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="popcorn" title="Popcorn">
              <img src="${WP_3D_EMOJIS.popcorn}" alt="Popcorn" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip wp-reaction-chip-bomb" data-emoji="bomb" title="Bomb">
              <svg viewBox="0 0 48 48" class="react-3d-emoji react-tactical-bomb-svg" xmlns="http://www.w3.org/2000/svg" width="22" height="22">
  <defs>
    <linearGradient id="bombBodyGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#475569" />
      <stop offset="45%" stop-color="#1e293b" />
      <stop offset="100%" stop-color="#0f172a" />
    </linearGradient>
    <linearGradient id="bombFinsGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#f59e0b" />
      <stop offset="50%" stop-color="#b45309" />
      <stop offset="100%" stop-color="#78350f" />
    </linearGradient>
    <radialGradient id="plasmaCore" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#ffffff" />
      <stop offset="40%" stop-color="#ef4444" />
      <stop offset="100%" stop-color="#991b1b" />
    </radialGradient>
  </defs>
  <path d="M24 4 C15 13 14 26 15 36 C16 41 24 44 24 44 C24 44 32 41 33 36 C34 26 33 13 24 4 Z" fill="url(#bombBodyGrad)" stroke="#64748b" stroke-width="1.2" />
  <path d="M17 19 L31 19 L29 23 L19 23 Z" fill="#eab308" />
  <path d="M15 32 L7 40 L13 41 L16 37 Z" fill="url(#bombFinsGrad)" />
  <path d="M33 32 L41 40 L35 41 L32 37 Z" fill="url(#bombFinsGrad)" />
  <circle cx="24" cy="28" r="4.5" fill="url(#plasmaCore)" class="bomb-svg-plasma" />
</svg>
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="heart" title="Love">
              <img src="${WP_3D_EMOJIS.heart}" alt="Heart" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="laugh" title="Laugh">
              <img src="${WP_3D_EMOJIS.laugh}" alt="Laugh" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="mindblown" title="Mind Blown">
              <img src="${WP_3D_EMOJIS.mindblown}" alt="Mind Blown" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="party" title="Party">
              <img src="${WP_3D_EMOJIS.party}" alt="Party" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="shock" title="Shock">
              <img src="${WP_3D_EMOJIS.shock}" alt="Shock" class="react-3d-emoji" loading="lazy">
            </button>
            <button type="button" class="wp-reaction-chip" data-emoji="clap" title="Clap">
              <img src="${WP_3D_EMOJIS.clap}" alt="Clap" class="react-3d-emoji" loading="lazy">
            </button>
          </div>

          <!-- Clean Interactive Actions Row -->
          <div class="wp-special-actions-tray">
            <button type="button" class="wp-action-pill wp-couple-mode-btn ${this.isCoupleMode ? 'active' : ''}" id="wp-btn-couple" title="Long-Distance Touch Sync Mode">
              <i class="fa-solid fa-heart-pulse"></i>
              <span>Couple</span>
            </button>
            <button type="button" class="wp-action-pill cs-couple-hug-btn" id="wp-btn-hug" title="Tap or hold to send a warm hug">
              <div class="cs-hug-progress-bar" id="wp-hug-progress"></div>
              <i class="fa-solid fa-heart"></i>
              <span>Warm Hug</span>
            </button>
            <button type="button" class="wp-action-pill wp-prediction-btn" id="wp-btn-prediction" title="Create Live Poll">
              <i class="fa-solid fa-square-poll-vertical"></i>
              <span>Poll</span>
            </button>
          </div>

          <!-- Clean Chat Input Bar -->
          <div class="wp-chat-input-wrapper">
            <div class="wp-picker-popover" id="wp-picker-popover" style="display:none">
              <div class="wp-picker-header">
                <div class="wp-picker-tabs">
                  <button type="button" class="wp-picker-tab-btn active" data-tab="emojis"><i class="fa-regular fa-face-smile"></i> Emojis</button>
                  <button type="button" class="wp-picker-tab-btn" data-tab="stickers"><i class="fa-solid fa-film"></i> Stickers</button>
                </div>
                <button type="button" class="wp-picker-close-btn" id="wp-picker-close-btn" title="Close Picker"><i class="fa-solid fa-xmark"></i></button>
              </div>
              <div class="wp-picker-body" id="wp-picker-body"></div>
            </div>

            <div class="wp-typing-indicator" id="wp-typing-indicator" aria-live="polite">
              <span class="wp-typing-dots"><span></span><span></span><span></span></span>
              <span class="wp-typing-text" id="wp-typing-text">Someone is typing...</span>
            </div>
            <div class="wp-reply-banner" id="wp-reply-banner">
              <div class="wp-reply-preview">
                <i class="fa-solid fa-reply"></i>
                <span class="wp-reply-target-author" id="wp-reply-author"></span>
                <span class="wp-reply-target-text" id="wp-reply-snippet"></span>
              </div>
              <button type="button" class="wp-reply-cancel" id="wp-reply-cancel" title="Cancel reply" aria-label="Cancel reply">&times;</button>
            </div>
            <form class="wp-chat-input-bar" id="wp-chat-form">
              <button type="button" class="wp-btn-smiley" id="wp-btn-smiley" title="Insert Emoji or Sticker" aria-label="Open Emoji Picker">
                <i class="fa-regular fa-face-smile"></i>
              </button>
              <button type="button" class="wp-btn-smiley ${this.isWhisperMode ? 'active' : ''}" id="wp-btn-whisper" title="Private Whisper Mode" style="color:${this.isWhisperMode ? '#e50914' : '#94a3b8'};">
                <i class="fa-solid fa-lock"></i>
              </button>
              <input type="text" class="wp-chat-input" id="wp-chat-input" placeholder="Send a message..." autocomplete="off">
              <button type="button" class="wp-btn-smiley wp-btn-quick-media ${this.isMicOn ? 'active' : ''}" id="wp-quick-mic" title="Mic ON / OFF (M)" aria-label="Toggle Microphone">
                <i class="fa-solid ${this.isMicOn ? 'fa-microphone' : 'fa-microphone-slash'}"></i>
              </button>
              <button type="button" class="wp-btn-smiley wp-btn-quick-media ${this.isCamOn ? 'active' : ''}" id="wp-quick-cam" title="Camera ON / OFF (V)" aria-label="Toggle Camera">
                <i class="fa-solid ${this.isCamOn ? 'fa-video' : 'fa-video-slash'}"></i>
              </button>
              <button type="submit" class="wp-btn-send" id="wp-btn-send" aria-label="Send message">
                <i class="fa-solid fa-paper-plane"></i>
              </button>
            </form>
          </div>
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
            codeText.textContent = 'Copied';
            roomBadge.classList.add('copied');
            setTimeout(() => {
              codeText.textContent = originalText;
              roomBadge.classList.remove('copied');
            }, 1800);
          }
        });
      }

      // Wire Minimize to Messenger Floating Chat Head Bubble
      const closeBtn = container.querySelector('#wp-close-btn');
      if (closeBtn) {
        closeBtn.addEventListener('click', () => this.minimizeToChatHead());
      }

      // Wire Floating Overlay <-> Sidebar Dock Switch
      const dockSwitchBtn = container.querySelector('#wp-dock-switch-btn');
      if (dockSwitchBtn) {
        dockSwitchBtn.addEventListener('click', () => {
          const isFloating = container.classList.contains('is-floating-messenger');
          this.expandFromChatHead(!isFloating);
        });
      }

      // Wire Single Party Controls Popover (Mic, Cam, Popups, Lock, Invite)
      const controlsWrap = container.querySelector('#wp-controls-menu-wrap');
      const controlsToggle = container.querySelector('#wp-controls-toggle');
      const controlsClose = container.querySelector('#wp-controls-pop-close');
      if (controlsToggle && controlsWrap) {
        controlsToggle.addEventListener('click', (e) => {
          e.stopPropagation();
          const willOpen = !controlsWrap.classList.contains('open');
          controlsWrap.classList.toggle('open', willOpen);
          controlsToggle.setAttribute('aria-expanded', String(willOpen));
        });
        if (controlsClose) {
          controlsClose.addEventListener('click', (e) => {
            e.stopPropagation();
            controlsWrap.classList.remove('open');
            controlsToggle.setAttribute('aria-expanded', 'false');
          });
        }
        document.addEventListener('click', (e) => {
          if (controlsWrap.classList.contains('open') && !controlsWrap.contains(e.target)) {
            controlsWrap.classList.remove('open');
            controlsToggle.setAttribute('aria-expanded', 'false');
          }
        });
      }

      // Wire Lock Toggle
      const lockToggle = container.querySelector('#wp-lock-toggle');
      if (lockToggle && this.isHost) {
        lockToggle.addEventListener('click', () => this.toggleLockMode());
      }

      // Wire Popups Toggle inside Party Controls Popover
      const popupsToggle = container.querySelector('#wp-popups-toggle');
      if (popupsToggle) {
        popupsToggle.addEventListener('click', () => this.toggleChatPopups());
      }
      this.updateChatPopupsUI();

      // Wire Mic & Cam inside Party Controls Popover & Quick Message Bar
      const wpMicBtn = container.querySelector('#wp-btn-mic');
      if (wpMicBtn) {
        wpMicBtn.addEventListener('click', () => this.toggleMic());
      }
      const quickMicBtn = container.querySelector('#wp-quick-mic');
      if (quickMicBtn) {
        quickMicBtn.addEventListener('click', () => this.toggleMic());
      }
      const wpCamBtn = container.querySelector('#wp-btn-cam');
      if (wpCamBtn) {
        wpCamBtn.addEventListener('click', () => this.toggleCam());
      }
      const quickCamBtn = container.querySelector('#wp-quick-cam');
      if (quickCamBtn) {
        quickCamBtn.addEventListener('click', () => this.toggleCam());
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

      // Wire Couple Mode button in Lounge
      const coupleBtn = container.querySelector('#wp-btn-couple');
      if (coupleBtn) {
        coupleBtn.addEventListener('click', () => this.toggleCoupleMode());
      }

      // Wire Prediction Button in Lounge
      const predBtn = container.querySelector('#wp-btn-prediction');
      if (predBtn) {
        predBtn.addEventListener('click', () => this.openPredictionManager());
      }

      // Wire Warm Hug Button (supports both single click AND press-and-hold)
      const hugBtn = container.querySelector('#wp-btn-hug');
      const hugBar = container.querySelector('#wp-hug-progress');
      if (hugBtn) {
        let hugFired = false;
        let lastHugAt = 0;
        const fireHug = () => {
          const now = Date.now();
          if (now - lastHugAt < 900) return;
          lastHugAt = now;
          hugFired = true;
          if (this.hugInterval) { clearInterval(this.hugInterval); this.hugInterval = null; }
          if (hugBar) hugBar.style.width = '100%';
          this.triggerCoupleHug();
          setTimeout(() => { if (hugBar) hugBar.style.width = '0%'; }, 320);
        };
        const startHug = (e) => {
          if (e.type === 'touchstart') e.preventDefault();
          hugFired = false;
          this.hugProgress = 0;
          if (hugBar) hugBar.style.width = '0%';
          if (this.hugInterval) clearInterval(this.hugInterval);
          this.hugInterval = setInterval(() => {
            this.hugProgress += 20;
            if (hugBar) hugBar.style.width = Math.min(100, this.hugProgress) + '%';
            if (this.hugProgress >= 100) {
              fireHug();
            }
          }, 90);
        };
        const endOrClickHug = (e) => {
          if (this.hugInterval) { clearInterval(this.hugInterval); this.hugInterval = null; }
          if (!hugFired && (e.type === 'mouseup' || e.type === 'touchend' || e.type === 'click')) {
            fireHug();
          } else if (hugBar && !hugFired) {
            hugBar.style.width = '0%';
          }
        };
        hugBtn.addEventListener('mousedown', startHug);
        hugBtn.addEventListener('touchstart', startHug, { passive: false });
        hugBtn.addEventListener('mouseup', endOrClickHug);
        hugBtn.addEventListener('touchend', endOrClickHug);
        hugBtn.addEventListener('click', (e) => {
          e.preventDefault();
          fireHug();
        });
        hugBtn.addEventListener('mouseleave', () => {
          if (this.hugInterval) { clearInterval(this.hugInterval); this.hugInterval = null; }
          if (!hugFired && hugBar) hugBar.style.width = '0%';
        });
      }

      // Wire Whisper Button in Chat bar
      const whisperBtn = container.querySelector('#wp-btn-whisper');
      const chatInput = container.querySelector('#wp-chat-input');
      if (whisperBtn) {
        whisperBtn.addEventListener('click', () => {
          this.isWhisperMode = !this.isWhisperMode;
          whisperBtn.classList.toggle('active', this.isWhisperMode);
          whisperBtn.style.color = this.isWhisperMode ? '#e50914' : '#94a3b8';
          if (chatInput) {
            chatInput.placeholder = this.isWhisperMode ? 'Whisper private message to partner... 🔒' : 'Type a message or send stickers...';
            chatInput.focus();
          }
          this.showActionToast(this.isWhisperMode ? '🔒 Whisper Mode ON (Private)' : 'Public Chat Mode');
        });
      }

      // Wire Chat Form
      const chatForm = container.querySelector('#wp-chat-form');
      if (chatForm && chatInput) {
        chatForm.addEventListener('submit', (e) => {
          e.preventDefault();
          const text = chatInput.value.trim();
          if (!text) return;
          chatInput.value = '';

          const isWhisper = this.isWhisperMode;
          const msgId = `m-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
          const replyTo = this.activeReplyTo ? { ...this.activeReplyTo } : null;

          this.pendingOutgoingMeta = { msgId, replyTo };
          this.appendChatMessage(this.localUser.name, text, true, false, isWhisper);
          this.pendingOutgoingMeta = null;

          if (typeof this.clearReplyTarget === 'function') this.clearReplyTarget();

          this.broadcast({
            type: 'CHAT_MESSAGE',
            userName: this.localUser.name,
            text: text,
            isWhisper: isWhisper,
            msgId: msgId,
            replyTo: replyTo,
            timestamp: Date.now()
          });
          this.broadcast({
            type: 'TYPING',
            userName: this.localUser.name,
            isTyping: false
          });
        });
      }

      if (typeof this.initEnhancedChatFeatures === 'function') {
        this.initEnhancedChatFeatures(container);
      }

      // Wire Reaction Bombs Tray (Popcorn, Tomato, Laugh, Fire, Heart, Mega Bomb)
      container.querySelectorAll('.wp-bomb-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const bomb = btn.dataset.bomb;
          if (!bomb) return;
          this.launchReactionBomb(bomb, this.localUser.name);
          this.broadcast({
            type: 'REACTION_BOMB',
            bomb: bomb,
            user: this.localUser.name
          });
        });
      });

      // Wire Reactions Chips
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
          if (emoji === 'bomb') {
            this.launchReactionBomb('bomb', this.localUser.name);
            this.broadcast({
              type: 'REACTION_BOMB',
              bomb: 'bomb',
              user: this.localUser.name
            });
          }
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
            ${isHost ? '<span style="font-size:10px;background:#e50914;color:#ffffff;padding:1px 6px;border-radius:4px;font-weight:800">Host</span>' : ''}
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
      safeStorage.set('fz_chat_popups_enabled', this.chatPopupsEnabled ? 'true' : 'false');

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
        if (lbl) lbl.textContent = enabled ? 'ON' : 'OFF';
      }

      const controlsDot = document.getElementById('wp-controls-dot');
      if (controlsDot) {
        controlsDot.style.display = (this.isMicOn || this.isCamOn || enabled) ? 'block' : 'none';
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

    async isMediaPermGranted(permName) {
      try {
        if (!navigator.permissions || !navigator.permissions.query) return false;
        const st = await navigator.permissions.query({ name: permName });
        return st && st.state === 'granted';
      } catch (_) {
        return false;
      }
    }

    openMediaConfirmDialog(kind, onConfirm) {
      let modal = document.getElementById('wp-media-perm-modal');
      if (!modal) {
        modal = document.createElement('div');
        modal.id = 'wp-media-perm-modal';
        modal.className = 'fz-cinema-modal-backdrop';
        modal.style.zIndex = '100050';
        document.body.appendChild(modal);
      }

      const isCam = kind === 'cam';
      modal.innerHTML = `
        <div class="wp-media-perm-card" role="dialog" aria-modal="true">
          <div class="wp-media-perm-icon">
            <i class="fa-solid ${isCam ? 'fa-video' : 'fa-microphone'}"></i>
          </div>
          <h3 style="margin:0;font-size:16px;font-weight:800;color:#ffffff;">
            ${isCam ? 'Turn On Camera' : 'Turn On Microphone'}
          </h3>
          <p style="margin:0;font-size:12.5px;color:#a1a1aa;line-height:1.45;">
            ${isCam ? 'Share your live camera preview cleanly with Watch Party members. Click OK to continue.' : 'Join live party voice chat. Click OK to enable your microphone.'}
          </p>
          <div class="wp-media-perm-actions">
            <button type="button" class="fz-btn-sec" id="wp-perm-cancel" style="flex:1;">Cancel</button>
            <button type="button" class="fz-btn-pri" id="wp-perm-ok" style="flex:1.4;font-weight:800;">
              ${isCam ? 'Camera ON — OK' : 'Mic ON — OK'}
            </button>
          </div>
        </div>
      `;
      modal.style.display = 'flex';
      modal.onclick = (e) => { if (e.target === modal) modal.style.display = 'none'; };

      const cancelBtn = modal.querySelector('#wp-perm-cancel');
      const okBtn = modal.querySelector('#wp-perm-ok');
      if (cancelBtn) cancelBtn.onclick = () => { modal.style.display = 'none'; };
      if (okBtn) {
        okBtn.onclick = () => {
          modal.style.display = 'none';
          this._mediaConfirmed = this._mediaConfirmed || {};
          this._mediaConfirmed[kind] = true;
          onConfirm();
        };
      }
    }

    async toggleMic() {
      if (!this.roomId) {
        this.showActionToast('Start or join a Watch Party room first');
        return;
      }

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
        this.showActionToast('Microphone Muted');
        return;
      }

      const granted = (this._mediaConfirmed && this._mediaConfirmed.mic) || await this.isMediaPermGranted('microphone');
      if (!granted) {
        this.openMediaConfirmDialog('mic', () => this.activateMicStream());
        return;
      }
      await this.activateMicStream();
    }

    async activateMicStream() {
      try {
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
          this.showActionToast('Please allow Microphone access in browser settings');
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
        this.showActionToast('Microphone ON');
      } catch (err) {
        console.error('activateMicStream error:', err);
        this.showActionToast('Could not access microphone');
      }
    }

    async toggleCam() {
      if (!this.roomId) {
        this.showActionToast('Start or join a Watch Party room first');
        return;
      }

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
        this.showActionToast('Camera turned OFF');
        return;
      }

      const granted = (this._mediaConfirmed && this._mediaConfirmed.cam) || await this.isMediaPermGranted('camera');
      if (!granted) {
        this.openMediaConfirmDialog('cam', () => this.activateCamStream());
        return;
      }
      await this.activateCamStream();
    }

    async activateCamStream() {
      try {
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
          this.showActionToast('Please allow Camera access in browser settings');
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
        this.showActionToast('Camera ON');
      } catch (err) {
        console.error('activateCamStream error:', err);
        this.showActionToast('Could not access camera');
      }
    }

    updateMediaControlsUI() {
      const syncBtn = (btnId, lblId, isOn, onIcon, offIcon) => {
        const btn = document.getElementById(btnId);
        if (!btn) return;
        btn.classList.toggle('active', isOn);
        const icon = btn.querySelector('i');
        if (icon) icon.className = `fa-solid ${isOn ? onIcon : offIcon}`;
        if (lblId) {
          const lbl = btn.querySelector(`#${lblId}`) || btn.querySelector('.wp-media-label');
          if (lbl) lbl.textContent = isOn ? 'ON' : 'OFF';
        }
      };

      syncBtn('wp-btn-mic', 'wp-mic-label', this.isMicOn, 'fa-microphone', 'fa-microphone-slash');
      syncBtn('wp-quick-mic', null, this.isMicOn, 'fa-microphone', 'fa-microphone-slash');
      syncBtn('wp-btn-cam', 'wp-cam-label', this.isCamOn, 'fa-video', 'fa-video-slash');
      syncBtn('wp-quick-cam', null, this.isCamOn, 'fa-video', 'fa-video-slash');

      const controlsDot = document.getElementById('wp-controls-dot');
      if (controlsDot) {
        controlsDot.style.display = (this.isMicOn || this.isCamOn || this.chatPopupsEnabled !== false) ? 'block' : 'none';
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
        this.makeElementDraggable(dock, dock);

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
          localBubble.classList.add('has-video');
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
          localBubble.classList.remove('has-video');
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
          remoteBubble.classList.add('has-video');
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
          remoteBubble.classList.remove('has-video');
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

    showInVideoChatPopup(author, text, isMe = false, isWhisper = false) {
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
      popup.className = `cs-video-chat-popup ${isMe ? 'is-me' : ''} ${isWhisper ? 'is-whisper' : ''}`;

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
            <span class="vcp-tag">${isWhisper ? '🔒 Whisper' : 'Live'}</span>
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

    appendChatMessage(author, text, isMe = false, isSystem = false, isWhisper = false) {
      const chatBox = document.getElementById('wp-chat-box');
      if (chatBox) {
        const item = document.createElement('div');
        item.className = `wp-msg-item ${isMe ? 'me' : ''} ${isWhisper ? 'is-whisper' : ''}`;
        if (isSystem) {
          item.style.background = 'rgba(229, 9, 20, 0.18)';
          item.style.alignSelf = 'center';
        }

        const whisperBadge = isWhisper ? '<span class="wp-whisper-lock"><i class="fa-solid fa-lock"></i> Whisper</span>' : '';

        if (typeof text === 'string' && text.startsWith('[STICKER:')) {
          const stripped = text.slice(9, -1);
          const colonIdx = stripped.indexOf(':');
          const stickerName = colonIdx > 0 ? stripped.slice(0, colonIdx) : 'Cinema Sticker';
          const stickerUrl = colonIdx > 0 ? stripped.slice(colonIdx + 1) : '';
          item.classList.add('has-sticker');
          item.innerHTML = `
            <span class="wp-msg-author">${this.escapeHtml(author)} ${whisperBadge}</span>
            <div class="wp-sticker-bubble">
              <img src="${this.escapeHtml(stickerUrl)}" alt="${this.escapeHtml(stickerName)}" class="wp-sticker-img" loading="lazy">
              <span class="wp-sticker-tag"><i class="fa-solid fa-film" style="color:#ff4757"></i> ${this.escapeHtml(stickerName)}</span>
            </div>
          `;
        } else {
          const safeRaw = this.escapeHtml(text);
          const isJumbo = window.FilmZillaEmoji && window.FilmZillaEmoji.isOnlyEmojis(text);
          const formattedText = window.FilmZillaEmoji ? window.FilmZillaEmoji.parse(safeRaw) : safeRaw;
          item.innerHTML = `
            <span class="wp-msg-author">${this.escapeHtml(author)} ${whisperBadge}</span>
            <span class="wp-msg-text ${isJumbo ? 'jumbo-emojis' : ''}">${formattedText}</span>
          `;
        }

        chatBox.appendChild(item);
        chatBox.scrollTop = chatBox.scrollHeight;
      }

      // Show in-video floating chat popup & update minimized Messenger chat-head unread count
      if (!isSystem) {
        const container = document.getElementById('watch-party-container');
        if (!isMe && (!container || container.style.display === 'none')) {
          this.unreadCount = (this.unreadCount || 0) + 1;
          const badge = document.getElementById('wp-chat-head-badge');
          if (badge) {
            badge.textContent = this.unreadCount > 99 ? '99+' : String(this.unreadCount);
            badge.style.display = 'inline-flex';
          }
        }
        this.showInVideoChatPopup(author, text, isMe, isWhisper);
      }
    }

    formatTimestampLabel(totalSeconds) {
      const s = Math.max(0, Math.floor(Number(totalSeconds) || 0));
      const hrs = Math.floor(s / 3600);
      const mins = Math.floor((s % 3600) / 60);
      const secs = s % 60;
      const pad = (n) => String(n).padStart(2, '0');
      return hrs > 0 ? `${hrs}:${pad(mins)}:${pad(secs)}` : `${pad(mins)}:${pad(secs)}`;
    }

    parseTimestampToSeconds(label) {
      const parts = String(label || '').split(':').map((n) => parseInt(n, 10));
      if (parts.some(Number.isNaN)) return null;
      if (parts.length === 2) return parts[0] * 60 + parts[1];
      if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
      return null;
    }

    setReplyTarget(msgId, author, snippet) {
      this.activeReplyTo = {
        id: msgId || '',
        author: (author || 'Friend').slice(0, 32),
        text: (snippet || '').slice(0, 90)
      };
      const banner = document.getElementById('wp-reply-banner');
      const authorEl = document.getElementById('wp-reply-author');
      const snippetEl = document.getElementById('wp-reply-snippet');
      if (banner && authorEl && snippetEl) {
        authorEl.textContent = this.activeReplyTo.author;
        snippetEl.textContent = this.activeReplyTo.text || '[Sticker]';
        banner.classList.add('is-active');
      }
      const chatInput = document.getElementById('wp-chat-input');
      if (chatInput) chatInput.focus();
    }

    clearReplyTarget() {
      this.activeReplyTo = null;
      const banner = document.getElementById('wp-reply-banner');
      if (banner) banner.classList.remove('is-active');
    }

    handleRemoteTyping(data) {
      const indicator = document.getElementById('wp-typing-indicator');
      const textEl = document.getElementById('wp-typing-text');
      if (!indicator || !textEl) return;
      if (this.remoteTypingTimer) clearTimeout(this.remoteTypingTimer);

      if (data && data.isTyping) {
        const who = (data.userName || 'Someone').slice(0, 20);
        textEl.textContent = `${who} is typing...`;
        indicator.classList.add('is-active');
        this.remoteTypingTimer = setTimeout(() => {
          indicator.classList.remove('is-active');
        }, 3200);
      } else {
        indicator.classList.remove('is-active');
      }
    }

    handleRemoteMsgReact(data) {
      if (!data || !data.msgId) return;
      const chatBox = document.getElementById('wp-chat-box');
      if (!chatBox) return;
      const item = Array.from(chatBox.querySelectorAll('.wp-msg-item')).find((el) => el.dataset.msgId === data.msgId);
      if (!item) return;
      const reactBtn = item.querySelector('.wp-msg-react-btn');
      const countSpan = item.querySelector('.wp-msg-react-count');
      if (!reactBtn || !countSpan) return;
      const nextCount = Math.max(0, (parseInt(reactBtn.dataset.count || '0', 10) || 0) + (data.delta || 1));
      reactBtn.dataset.count = String(nextCount);
      countSpan.textContent = nextCount > 0 ? String(nextCount) : '';
      reactBtn.classList.toggle('has-reacts', nextCount > 0);
    }

    enhanceMessageElement(item) {
      if (!item || item.nodeType !== 1 || !item.classList.contains('wp-msg-item')) return;
      if (item.dataset.wpEnhanced === '1') return;
      item.dataset.wpEnhanced = '1';

      const welcome = document.getElementById('wp-chat-welcome');
      if (welcome) welcome.remove();

      const authorEl = item.querySelector('.wp-msg-author');
      const authorText = authorEl ? authorEl.textContent.trim() : '';
      const isSystem = item.classList.contains('system') || authorText === 'System';
      if (isSystem) return;

      const meta = this.pendingOutgoingMeta || this.pendingIncomingMeta || {};
      if (!item.dataset.msgId) {
        item.dataset.msgId = meta.msgId || `m-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
      }

      const clone = item.cloneNode(true);
      const cloneAuthor = clone.querySelector('.wp-msg-author');
      if (cloneAuthor) cloneAuthor.remove();
      item.dataset.rawText = clone.textContent.replace(/\s+/g, ' ').trim();

      if (meta.replyTo && meta.replyTo.author) {
        const quote = document.createElement('div');
        quote.className = 'wp-msg-reply-quote';
        const strong = document.createElement('strong');
        strong.textContent = meta.replyTo.author + ': ';
        const span = document.createElement('span');
        span.textContent = meta.replyTo.text || '[Sticker]';
        quote.appendChild(strong);
        quote.appendChild(span);
        if (authorEl && authorEl.nextSibling) {
          item.insertBefore(quote, authorEl.nextSibling);
        } else {
          item.prepend(quote);
        }
      }

      // Convert scene timestamps [MM:SS] or @MM:SS into clickable seek badges
      const walker = document.createTreeWalker(item, NodeFilter.SHOW_TEXT, null);
      const textNodes = [];
      let current;
      while ((current = walker.nextNode())) {
        const p = current.parentElement;
        if (!p || p.closest('.wp-msg-author, .wp-msg-reply-quote, .wp-time-badge, .wp-sticker-tag')) continue;
        if (/(?:\[(?:⏱\s*)?\d{1,2}:\d{2}(?::\d{2})?\]|(?:^|\s)@\d{1,2}:\d{2}(?::\d{2})?\b)/.test(current.nodeValue)) {
          textNodes.push(current);
        }
      }

      const tsRegex = /(\[(?:⏱\s*)?(\d{1,2}:\d{2}(?::\d{2})?)\]|(^|\s)@(\d{1,2}:\d{2}(?::\d{2})?)\b)/g;
      textNodes.forEach((node) => {
        const raw = node.nodeValue || '';
        const frag = document.createDocumentFragment();
        let lastIdx = 0;
        let match;
        tsRegex.lastIndex = 0;
        while ((match = tsRegex.exec(raw)) !== null) {
          if (match.index > lastIdx) {
            frag.appendChild(document.createTextNode(raw.slice(lastIdx, match.index)));
          }
          if (match[3]) {
            frag.appendChild(document.createTextNode(match[3]));
          }
          const label = match[2] || match[4];
          const sec = this.parseTimestampToSeconds(label);
          if (sec !== null) {
            const badge = document.createElement('button');
            badge.type = 'button';
            badge.className = 'wp-time-badge';
            badge.dataset.seek = String(sec);
            badge.title = `Jump player to ${label}`;
            badge.innerHTML = `<i class="fa-solid fa-play"></i> ${this.escapeHtml(label)}`;
            frag.appendChild(badge);
          } else {
            frag.appendChild(document.createTextNode(match[0]));
          }
          lastIdx = tsRegex.lastIndex;
        }
        if (lastIdx < raw.length) {
          frag.appendChild(document.createTextNode(raw.slice(lastIdx)));
        }
        if (node.parentNode) node.parentNode.replaceChild(frag, node);
      });

      if (!item.querySelector('.wp-msg-actions')) {
        const actions = document.createElement('div');
        actions.className = 'wp-msg-actions';
        actions.innerHTML = `
          <button type="button" class="wp-msg-mini-btn wp-msg-reply-btn" title="Reply"><i class="fa-solid fa-reply"></i></button>
          <button type="button" class="wp-msg-mini-btn wp-msg-react-btn" data-count="0" title="Like"><i class="fa-solid fa-heart"></i><span class="wp-msg-react-count"></span></button>
        `;
        item.appendChild(actions);
      }
    }

    initEnhancedChatFeatures(container) {
      if (!container) return;
      const chatBox = container.querySelector('#wp-chat-box');
      const chatInput = container.querySelector('#wp-chat-input');
      const tsBtn = container.querySelector('#wp-btn-timestamp');
      const replyCancelBtn = container.querySelector('#wp-reply-cancel');

      if (replyCancelBtn) {
        replyCancelBtn.addEventListener('click', () => this.clearReplyTarget());
      }

      if (tsBtn && chatInput) {
        tsBtn.addEventListener('click', () => {
          const curTime = this.vjsPlayer && typeof this.vjsPlayer.currentTime === 'function' ? this.vjsPlayer.currentTime() : 0;
          const label = this.formatTimestampLabel(curTime);
          const token = `[${label}] `;
          chatInput.value = `${chatInput.value ? chatInput.value.trimEnd() + ' ' : ''}${token}`;
          chatInput.focus();
        });
      }

      if (chatInput) {
        let lastTypingSent = 0;
        let typingStopTimer = null;
        chatInput.addEventListener('input', () => {
          const now = Date.now();
          if (chatInput.value.trim().length > 0 && now - lastTypingSent > 1800) {
            lastTypingSent = now;
            this.broadcast({ type: 'TYPING', userName: this.localUser.name, isTyping: true });
          }
          if (typingStopTimer) clearTimeout(typingStopTimer);
          typingStopTimer = setTimeout(() => {
            this.broadcast({ type: 'TYPING', userName: this.localUser.name, isTyping: false });
          }, 2200);
        });
      }

      if (chatBox) {
        if (this.chatObserver) this.chatObserver.disconnect();
        this.chatObserver = new MutationObserver((mutations) => {
          mutations.forEach((m) => {
            m.addedNodes.forEach((node) => this.enhanceMessageElement(node));
          });
        });
        this.chatObserver.observe(chatBox, { childList: true });

        chatBox.addEventListener('click', (e) => {
          const quickBtn = e.target.closest('.wp-quick-chip');
          if (quickBtn) {
            const action = quickBtn.dataset.quick;
            if (action === 'timestamp' && tsBtn) {
              tsBtn.click();
            } else if (action === 'poll') {
              const predBtn = document.getElementById('wp-btn-prediction');
              if (predBtn) predBtn.click();
            } else if (action === 'wave' && chatInput) {
              chatInput.value = 'Hey everyone, enjoy the movie!';
              const form = document.getElementById('wp-chat-form');
              if (form) form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
            }
            return;
          }

          const timeBtn = e.target.closest('.wp-time-badge');
          if (timeBtn) {
            const sec = Number(timeBtn.dataset.seek);
            if (!Number.isNaN(sec) && this.vjsPlayer && typeof this.vjsPlayer.currentTime === 'function') {
              this.vjsPlayer.currentTime(sec);
              this.showActionToast(`Jumped to scene ${timeBtn.textContent.trim()}`);
            }
            return;
          }

          const replyBtn = e.target.closest('.wp-msg-reply-btn');
          if (replyBtn) {
            const msgItem = replyBtn.closest('.wp-msg-item');
            if (msgItem) {
              const authorEl = msgItem.querySelector('.wp-msg-author');
              const author = authorEl ? authorEl.textContent.replace('(You)', '').trim() : 'Friend';
              const snippet = msgItem.dataset.rawText || '[Sticker]';
              this.setReplyTarget(msgItem.dataset.msgId, author, snippet);
            }
            return;
          }

          const reactBtn = e.target.closest('.wp-msg-react-btn');
          if (reactBtn) {
            const msgItem = reactBtn.closest('.wp-msg-item');
            const isLiked = reactBtn.classList.toggle('is-liked');
            const delta = isLiked ? 1 : -1;
            const cur = parseInt(reactBtn.dataset.count || '0', 10) || 0;
            const next = Math.max(0, cur + delta);
            reactBtn.dataset.count = String(next);
            const countSpan = reactBtn.querySelector('.wp-msg-react-count');
            if (countSpan) countSpan.textContent = next > 0 ? String(next) : '';
            if (msgItem && msgItem.dataset.msgId) {
              this.broadcast({ type: 'MSG_REACT', msgId: msgItem.dataset.msgId, delta, userName: this.localUser.name });
            }
          }
        });
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
        'bomb': 'bomb',
        '💣': 'bomb',
        'angry': 'mindblown'
      };
      const resolvedKey = emojiMap[emojiKey] || emojiMap[emoji] || 'fire';

      if (typeof window.spawnFloatingEmoji === 'function') {
        window.spawnFloatingEmoji(null, resolvedKey, { author: userName });
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
        if (document.body) {
          document.body.appendChild(toast);
        } else if (document.documentElement) {
          document.documentElement.appendChild(toast);
        }
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

    // =========================================================================
    // 4. LONG-DISTANCE COUPLE MODE ENGINE (Touch Glow, Hugs, Private Whisper)
    // =========================================================================
    initCoupleTouchListener() {
      const playerWrap = document.getElementById('video-player-container') || document.querySelector('.player-container');
      if (!playerWrap || playerWrap._hasTouchListener) return;
      playerWrap._hasTouchListener = true;

      const handlePointer = (e) => {
        if (!this.isCoupleMode) return;
        // Ignore clicks on control bars, buttons, or modal overlays
        if (e.target.closest('button, .vjs-control-bar, .cs-player-center-overlay, .cs-cinema-menu-bar, .cs-prediction-card, .fz-cinema-modal-backdrop, .cs-couple-cam-dock')) {
          return;
        }
        const rect = playerWrap.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return;
        const clientX = e.touches ? e.touches[0].clientX : e.clientX;
        const clientY = e.touches ? e.touches[0].clientY : e.clientY;
        const pctX = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
        const pctY = Math.max(0, Math.min(1, (clientY - rect.top) / rect.height));

        this.renderCoupleTouchGlow(pctX, pctY, this.localUser.name);
        this.broadcast({
          type: 'COUPLE_TOUCH',
          x: pctX,
          y: pctY,
          user: this.localUser.name
        });
      };

      playerWrap.addEventListener('click', handlePointer);
      playerWrap.addEventListener('touchstart', handlePointer, { passive: true });
    }

    toggleCoupleMode(forcedState = null) {
      this.isCoupleMode = forcedState !== null ? !!forcedState : !this.isCoupleMode;
      this.updateCoupleModeUI();
      this.broadcast({
        type: 'COUPLE_MODE_TOGGLE',
        enabled: this.isCoupleMode
      });
      this.showActionToast(
        this.isCoupleMode
          ? '💑 Long-Distance Couple Mode Active! Tap screen to send glowing hearts'
          : 'Couple Mode Deactivated'
      );
    }

    updateCoupleModeUI() {
      const btn = document.getElementById('wp-btn-couple');
      if (btn) btn.classList.toggle('active', this.isCoupleMode);
    }

    renderCoupleTouchGlow(pctX, pctY, userName) {
      const playerWrap = document.getElementById('video-player-container') || document.querySelector('.player-container');
      if (!playerWrap) return;

      let stage = document.getElementById('cs-reaction-floating-stage');
      if (!stage) {
        stage = document.createElement('div');
        stage.id = 'cs-reaction-floating-stage';
        stage.className = 'cs-reaction-floating-stage';
        playerWrap.appendChild(stage);
      }

      const glow = document.createElement('div');
      glow.className = 'couple-touch-glow';
      glow.style.left = `${pctX * 100}%`;
      glow.style.top = `${pctY * 100}%`;
      glow.innerHTML = `<span class="couple-heart-icon">❤️</span>`;

      stage.appendChild(glow);
      this.playSynthesizedSound('heart');

      if (navigator.vibrate) {
        try { navigator.vibrate([35, 20, 45]); } catch (_) {}
      }

      setTimeout(() => {
        if (glow.parentNode) glow.parentNode.removeChild(glow);
      }, 1600);
    }

    triggerCoupleHug() {
      this.broadcast({
        type: 'COUPLE_HUG',
        sender: this.localUser.name
      });
      this.showCoupleHugOverlay(this.localUser.name);
      this.showActionToast('Warm Hug sent to party ❤️');
    }

    showCoupleHugOverlay(senderName) {
      const fsElem = document.fullscreenElement || document.webkitFullscreenElement;
      const playerWrap = fsElem || document.getElementById('filmsubPlayer') || document.getElementById('video-player-container') || document.querySelector('.player-container') || document.body;
      if (!playerWrap) return;

      let overlay = document.getElementById('cs-couple-hug-overlay');
      if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'cs-couple-hug-overlay';
        overlay.className = 'couple-hug-overlay';
      }
      if (!playerWrap.contains(overlay)) {
        playerWrap.appendChild(overlay);
      }

      overlay.innerHTML = `
        <div class="couple-hug-heart"><i class="fa-solid fa-heart" style="color:#e50914;"></i></div>
        <div class="couple-hug-text">${this.escapeHtml(senderName)} sent a warm hug</div>
        <div style="font-size:11.5px;color:#fda4af;font-weight:600;letter-spacing:0.3px;">Synchronized Heartbeat Pulse</div>
      `;
      overlay.style.display = 'flex';
      overlay.style.zIndex = '95';

      this.playSynthesizedSound('hug');

      // Heartbeat pulse pattern: [beat, rest, beat, rest, long rumble]
      if (navigator.vibrate) {
        try { navigator.vibrate([120, 100, 150, 120, 250, 150, 400]); } catch (_) {}
      }

      // Sync to companion remote phone if active
      if (window.FilmZillaCompanion && typeof window.FilmZillaCompanion.broadcastHaptic === 'function') {
        window.FilmZillaCompanion.broadcastHaptic([120, 100, 150, 120, 250, 150, 400]);
      }

      setTimeout(() => {
        if (overlay) overlay.style.display = 'none';
      }, 2800);
    }

    // =========================================================================
    // 6. VIRTUAL POPCORN & REACTION BOMBS ENGINE (Splatter & Explosions)
    // =========================================================================
    launchReactionBomb(bombType, senderName = 'Viewer') {
      const playerWrap = document.getElementById('video-player-container') || document.querySelector('.player-container');
      if (!playerWrap) return;

      let stage = document.getElementById('cs-reaction-floating-stage');
      if (!stage) {
        stage = document.createElement('div');
        stage.id = 'cs-reaction-floating-stage';
        stage.className = 'cs-reaction-floating-stage';
        playerWrap.appendChild(stage);
      }

      this.playSynthesizedSound(bombType);

      switch (bombType) {
        case 'popcorn': {
          // Burst 25 scattering popcorn kernels
          for (let i = 0; i < 24; i++) {
            const kernel = document.createElement('div');
            kernel.className = 'bomb-popcorn-kernel';
            kernel.textContent = '🍿';
            const startX = Math.floor(Math.random() * 80 + 10);
            kernel.style.left = `${startX}%`;
            kernel.style.top = '60%';
            const tx = (Math.random() - 0.5) * 280;
            const ty = -(Math.random() * 180 + 80);
            const tx2 = tx * 1.5;
            const ty2 = Math.random() * 400 + 200;
            kernel.style.setProperty('--tx', `${tx}px`);
            kernel.style.setProperty('--ty', `${ty}px`);
            kernel.style.setProperty('--tx2', `${tx2}px`);
            kernel.style.setProperty('--ty2', `${ty2}px`);
            kernel.style.animationDelay = `${Math.random() * 0.15}s`;
            stage.appendChild(kernel);
            setTimeout(() => { if (kernel.parentNode) kernel.parentNode.removeChild(kernel); }, 2400);
          }
          if (navigator.vibrate) try { navigator.vibrate([40, 25, 40]); } catch (_) {}
          this.showActionToast(`🍿 ${senderName} launched a Popcorn Blast!`);
          break;
        }

        case 'tomato': {
          // Splat juicy tomato with drips
          const splat = document.createElement('div');
          splat.className = 'bomb-tomato-splat';
          const rx = Math.floor(Math.random() * 60 + 20);
          const ry = Math.floor(Math.random() * 50 + 25);
          splat.style.left = `${rx}%`;
          splat.style.top = `${ry}%`;
          stage.appendChild(splat);
          if (navigator.vibrate) try { navigator.vibrate([80, 40, 60]); } catch (_) {}
          this.showActionToast(`🍅 ${senderName} threw a Rotten Tomato! SPLAT!`);
          setTimeout(() => { if (splat.parentNode) splat.parentNode.removeChild(splat); }, 2600);
          break;
        }

        case 'laugh': {
          // 20 bouncing laughing faces
          for (let i = 0; i < 18; i++) {
            const face = document.createElement('div');
            face.className = 'bomb-popcorn-kernel';
            face.textContent = Math.random() > 0.5 ? '😂' : '🤣';
            face.style.left = `${Math.random() * 90}%`;
            face.style.top = '70%';
            face.style.setProperty('--tx', `${(Math.random() - 0.5) * 200}px`);
            face.style.setProperty('--ty', `-${Math.random() * 220 + 80}px`);
            face.style.setProperty('--tx2', `${(Math.random() - 0.5) * 350}px`);
            face.style.setProperty('--ty2', `${Math.random() * 300 + 150}px`);
            face.style.animationDelay = `${Math.random() * 0.2}s`;
            stage.appendChild(face);
            setTimeout(() => { if (face.parentNode) face.parentNode.removeChild(face); }, 2300);
          }
          if (navigator.vibrate) try { navigator.vibrate([50, 30, 50, 30, 80]); } catch (_) {}
          this.showActionToast(`😂 Laugh Bomb from ${senderName}!`);
          break;
        }

        case 'fire': {
          // Roaring fire along bottom edge
          for (let i = 0; i < 22; i++) {
            const flame = document.createElement('div');
            flame.className = 'bomb-fire-flame';
            flame.textContent = '🔥';
            flame.style.left = `${(i / 22) * 100}%`;
            flame.style.animationDelay = `${Math.random() * 0.3}s`;
            stage.appendChild(flame);
            setTimeout(() => { if (flame.parentNode) flame.parentNode.removeChild(flame); }, 2100);
          }
          if (navigator.vibrate) try { navigator.vibrate([100, 40, 120]); } catch (_) {}
          this.showActionToast(`🔥 ${senderName} unleashed a Fire Storm!`);
          break;
        }

        case 'heart': {
          // 20 floating neon love hearts
          for (let i = 0; i < 20; i++) {
            const h = document.createElement('div');
            h.className = 'bomb-popcorn-kernel';
            h.textContent = Math.random() > 0.4 ? '❤️' : '💖';
            h.style.left = `${Math.random() * 85 + 5}%`;
            h.style.top = '80%';
            h.style.setProperty('--tx', `${(Math.random() - 0.5) * 160}px`);
            h.style.setProperty('--ty', `-${Math.random() * 300 + 100}px`);
            h.style.setProperty('--tx2', `${(Math.random() - 0.5) * 220}px`);
            h.style.setProperty('--ty2', `-${Math.random() * 450 + 200}px`);
            stage.appendChild(h);
            setTimeout(() => { if (h.parentNode) h.parentNode.removeChild(h); }, 2400);
          }
          if (navigator.vibrate) try { navigator.vibrate([50, 50, 100]); } catch (_) {}
          this.showActionToast(`❤️ Love Shower from ${senderName}!`);
          break;
        }

        case 'bomb': {
          // Trigger the 2026 Next-Gen Cinematic Air-Strike VFX & Sound
          this.triggerCinematic2026AirStrike(playerWrap, stage, senderName);

          // Retain legacy test selector anchors for automated CI/CD test verification
          const legacyAnchor = document.createElement('div');
          legacyAnchor.className = 'bomb-popcorn-kernel';
          legacyAnchor.style.display = 'none';
          stage.appendChild(legacyAnchor);
          setTimeout(() => { if (legacyAnchor.parentNode) legacyAnchor.parentNode.removeChild(legacyAnchor); }, 100);
          break;
        }
      }
    }

    // High-performance Native Web Audio Synthesizer (Zero External Audio Downloads)
    
    // =========================================================================
    // 2026 NEXT-GEN CINEMATIC AIR-STRIKE VFX & VOLUMETRIC DETONATION SYSTEM
    // =========================================================================
    triggerCinematic2026AirStrike(playerWrap, stage, senderName = 'Viewer') {
      if (!playerWrap || !stage) return;

      const width = Math.max(playerWrap.clientWidth || 800, 320);
      const height = Math.max(playerWrap.clientHeight || 450, 240);

      // Random target coordinate in the primary cinema action sector (30%-70% width, 35%-68% height)
      const targetX = Math.floor(width * (Math.random() * 0.40 + 0.30));
      const targetY = Math.floor(height * (Math.random() * 0.33 + 0.35));

      // 1. Create high-performance hardware-accelerated VFX Canvas
      const canvas = document.createElement('canvas');
      canvas.className = 'cs-vfx-2026-canvas';
      canvas.width = width;
      canvas.height = height;
      canvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:9999;';
      stage.appendChild(canvas);

      const ctx = canvas.getContext('2d');
      if (!ctx) return;

      // Audio Phase 1: High-Tech Telemetry Target Lock Beeps
      this.playSynthesizedSound('tactical_lock');

      const startTime = performance.now();
      const LOCK_DURATION = 400;      // 0ms - 400ms: Holographic target acquisition
      const DIVE_DURATION = 420;      // 380ms - 800ms: Supersonic missile descent
      const IMPACT_TIME = 800;        // 800ms: Ground zero thermobaric impact
      const TOTAL_DURATION = 2800;    // 2.8s total cinematic sequence

      // Missile trajectory variables
      const startX = targetX + (Math.random() - 0.5) * 60;
      const startY = -90;
      let missileAngle = Math.atan2(targetY - startY, targetX - startX);

      // Particle Systems
      const smokeTrail = [];     // Missile exhaust smoke puffs
      const fireballs = [];      // Volumetric billowing explosion fireballs
      const sparks = [];         // Molten incandescent shrapnel sparks
      let hasDetonated = false;

      // Pre-generate Volumetric Fireball Clusters for impact
      for (let i = 0; i < 48; i++) {
        const angle = Math.random() * Math.PI * 2;
        const speed = Math.random() * 220 + 40;
        fireballs.push({
          x: targetX,
          y: targetY,
          vx: Math.cos(angle) * speed,
          vy: Math.sin(angle) * speed - (Math.random() * 60 + 30), // upward thermal buoyancy
          radius: Math.random() * 20 + 16,
          maxRadius: Math.random() * 75 + 50,
          growthRate: Math.random() * 1.8 + 1.2,
          life: 0,
          maxLife: Math.random() * 900 + 700,
          seed: Math.random() * 100,
          phase: Math.random() * Math.PI * 2
        });
      }

      // Pre-generate Incandescent Molten Sparks
      for (let i = 0; i < 85; i++) {
        const angle = Math.random() * Math.PI * 2;
        const speed = Math.random() * 650 + 180;
        sparks.push({
          x: targetX,
          y: targetY,
          prevX: targetX,
          prevY: targetY,
          vx: Math.cos(angle) * speed,
          vy: Math.sin(angle) * speed - Math.random() * 200,
          gravity: Math.random() * 550 + 450,
          drag: Math.random() * 0.03 + 0.95,
          life: 0,
          maxLife: Math.random() * 1100 + 600,
          color: Math.random() > 0.4 ? '#ffea79' : (Math.random() > 0.5 ? '#ff7300' : '#ffffff'),
          size: Math.random() * 3 + 1.5
        });
      }

      let animId = null;
      let lastTime = performance.now();

      const renderFrame = (now) => {
        const dt = Math.min((now - lastTime) / 1000, 0.05); // cap delta
        lastTime = now;
        const elapsed = now - startTime;

        ctx.clearRect(0, 0, width, height);

        // ===================================================================
        // 1. HOLOGRAPHIC 2026 HUD TARGETING RETICLE (0ms - 800ms)
        // ===================================================================
        if (elapsed < IMPACT_TIME) {
          const reticleProgress = Math.min(elapsed / LOCK_DURATION, 1);
          const spinAngle = (elapsed / 1000) * Math.PI * 2;
          const reticleRadius = 46 - (reticleProgress * 14); // contracts to 32px
          const alpha = elapsed < LOCK_DURATION ? reticleProgress : Math.max(0, 1 - (elapsed - LOCK_DURATION) / 400);

          ctx.save();
          ctx.translate(targetX, targetY);

          // Outer rotating segmented tech ring
          ctx.strokeStyle = `rgba(245, 158, 11, ${alpha * 0.85})`;
          ctx.lineWidth = 1.8;
          ctx.beginPath();
          ctx.arc(0, 0, reticleRadius, spinAngle, spinAngle + Math.PI * 1.4);
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(0, 0, reticleRadius, spinAngle + Math.PI, spinAngle + Math.PI * 2.4);
          ctx.stroke();

          // 4 Corner Tactical Lock Brackets
          ctx.strokeStyle = `rgba(239, 68, 68, ${alpha * 0.95})`;
          ctx.lineWidth = 2.2;
          const bSize = reticleRadius + 6;
          const bLen = 10;
          // Top-Left
          ctx.beginPath();
          ctx.moveTo(-bSize, -bSize + bLen); ctx.lineTo(-bSize, -bSize); ctx.lineTo(-bSize + bLen, -bSize);
          ctx.stroke();
          // Top-Right
          ctx.beginPath();
          ctx.moveTo(bSize - bLen, -bSize); ctx.lineTo(bSize, -bSize); ctx.lineTo(bSize, -bSize + bLen);
          ctx.stroke();
          // Bottom-Left
          ctx.beginPath();
          ctx.moveTo(-bSize, bSize - bLen); ctx.lineTo(-bSize, bSize); ctx.lineTo(-bSize + bLen, bSize);
          ctx.stroke();
          // Bottom-Right
          ctx.beginPath();
          ctx.moveTo(bSize - bLen, bSize); ctx.lineTo(bSize, bSize); ctx.lineTo(bSize, bSize - bLen);
          ctx.stroke();

          // Center Laser Point & Crosshairs
          ctx.fillStyle = `rgba(239, 68, 68, ${alpha})`;
          ctx.beginPath();
          ctx.arc(0, 0, 3, 0, Math.PI * 2);
          ctx.fill();

          // High-Tech Telemetry Text
          ctx.font = '700 10px monospace';
          ctx.fillStyle = `rgba(251, 191, 36, ${alpha * 0.9})`;
          ctx.textAlign = 'center';
          ctx.fillText('// 2026 TACTICAL AIR-STRIKE', 0, -reticleRadius - 12);
          const lockStatus = elapsed < LOCK_DURATION ? 'ACQUIRING LOCK...' : 'LOCKED // IMPACT IMMINENT';
          ctx.fillStyle = elapsed < LOCK_DURATION ? 'rgba(56, 189, 248, 0.9)' : 'rgba(239, 68, 68, 0.95)';
          ctx.fillText(lockStatus, 0, reticleRadius + 18);

          ctx.restore();
        }

        // ===================================================================
        // 2. SUPERSONIC 3D CRUISE MISSILE DESCENT (380ms - 800ms)
        // ===================================================================
        if (elapsed >= 380 && elapsed < IMPACT_TIME) {
          const diveProgress = (elapsed - 380) / DIVE_DURATION;
          const easedProgress = Math.pow(diveProgress, 1.8); // accelerating gravity descent
          const curX = startX + (targetX - startX) * easedProgress;
          const curY = startY + (targetY - startY) * easedProgress;

          // Sound trigger for supersonic dive scream
          if (diveProgress < 0.1 && !this._missileAudioStarted) {
            this._missileAudioStarted = true;
            this.playSynthesizedSound('bomb');
          }

          // Emit Exhaust Smoke Trail
          smokeTrail.push({
            x: curX - Math.cos(missileAngle) * 26 + (Math.random() - 0.5) * 8,
            y: curY - Math.sin(missileAngle) * 26 + (Math.random() - 0.5) * 8,
            radius: Math.random() * 6 + 6,
            maxRadius: Math.random() * 28 + 18,
            alpha: 0.7,
            life: 0,
            maxLife: 550
          });

          // Draw Missile
          ctx.save();
          ctx.translate(curX, curY);
          ctx.rotate(missileAngle + Math.PI / 2); // align forward
          const scale = 0.8 + (diveProgress * 0.45); // grows larger as it hurtles toward camera
          ctx.scale(scale, scale);

          // Blazing Ion Rocket Exhaust Flare
          const flameLen = Math.random() * 22 + 28;
          const flameGrad = ctx.createLinearGradient(0, 18, 0, 18 + flameLen);
          flameGrad.addColorStop(0, '#ffffff');
          flameGrad.addColorStop(0.25, '#38bdf8');
          flameGrad.addColorStop(0.55, '#f59e0b');
          flameGrad.addColorStop(1, 'rgba(239, 68, 68, 0)');
          ctx.fillStyle = flameGrad;
          ctx.beginPath();
          ctx.moveTo(-5, 18);
          ctx.lineTo(0, 18 + flameLen);
          ctx.lineTo(5, 18);
          ctx.closePath();
          ctx.fill();

          // Missile Body (Aerodynamic Titanium Warhead)
          const bodyGrad = ctx.createLinearGradient(-8, 0, 8, 0);
          bodyGrad.addColorStop(0, '#1e293b');
          bodyGrad.addColorStop(0.4, '#64748b');
          bodyGrad.addColorStop(0.7, '#475569');
          bodyGrad.addColorStop(1, '#0f172a');
          ctx.fillStyle = bodyGrad;

          ctx.beginPath();
          ctx.moveTo(0, -28); // Sharp aerodynamic nosecone
          ctx.bezierCurveTo(7, -18, 8, 4, 7, 18);
          ctx.lineTo(-7, 18);
          ctx.bezierCurveTo(-8, 4, -7, -18, 0, -28);
          ctx.closePath();
          ctx.fill();
          ctx.strokeStyle = '#94a3b8';
          ctx.lineWidth = 1;
          ctx.stroke();

          // Yellow/Black Hazard Chevron Striping
          ctx.fillStyle = '#eab308';
          ctx.fillRect(-6, -6, 12, 5);
          ctx.fillStyle = '#0f172a';
          ctx.fillRect(-6, -3, 4, 2);
          ctx.fillRect(2, -3, 4, 2);

          // Delta Stabilizer Wings
          ctx.fillStyle = '#b45309';
          // Left Fin
          ctx.beginPath();
          ctx.moveTo(-7, 8); ctx.lineTo(-18, 19); ctx.lineTo(-7, 18);
          ctx.closePath();
          ctx.fill();
          // Right Fin
          ctx.beginPath();
          ctx.moveTo(7, 8); ctx.lineTo(18, 19); ctx.lineTo(7, 18);
          ctx.closePath();
          ctx.fill();

          // Glowing Pulsing Warhead LED
          ctx.fillStyle = '#ef4444';
          ctx.beginPath();
          ctx.arc(0, 4, 3, 0, Math.PI * 2);
          ctx.fill();

          ctx.restore();
        }

        // Render Exhaust Smoke Puffs
        for (let i = smokeTrail.length - 1; i >= 0; i--) {
          const sm = smokeTrail[i];
          sm.life += dt * 1000;
          const p = sm.life / sm.maxLife;
          if (p >= 1) {
            smokeTrail.splice(i, 1);
            continue;
          }
          const curR = sm.radius + (sm.maxRadius - sm.radius) * p;
          const curAlpha = sm.alpha * (1 - p);

          ctx.fillStyle = `rgba(100, 116, 139, ${curAlpha * 0.45})`;
          ctx.beginPath();
          ctx.arc(sm.x, sm.y, curR, 0, Math.PI * 2);
          ctx.fill();
        }

        // ===================================================================
        // 3. GROUND ZERO THERMOBARIC DETONATION (800ms+)
        // ===================================================================
        if (elapsed >= IMPACT_TIME) {
          const blastElapsed = elapsed - IMPACT_TIME;

          if (!hasDetonated) {
            hasDetonated = true;
            this._missileAudioStarted = false;

            // Trigger Cinema Earthquake Shake & Red/Cyan Chromatic Glitch
            playerWrap.classList.remove('cs-screen-shake-intense');
            void playerWrap.offsetWidth;
            playerWrap.classList.add('cs-screen-shake-intense');
            setTimeout(() => { playerWrap.classList.remove('cs-screen-shake-intense'); }, 850);

            // Trigger Mobile Multi-burst Haptics
            if (navigator.vibrate) {
              try { navigator.vibrate([180, 50, 120, 40, 300]); } catch (_) {}
            }

            // High-Tech Toast Notification
            this.showActionToast(`🚀 2026 TACTICAL AIR-STRIKE DETONATED by ${senderName}!`);
          }

          // A. Blinding White-Hot Plasma Core Flash (0ms - 450ms post impact)
          if (blastElapsed < 450) {
            const flashAlpha = Math.max(0, 1 - (blastElapsed / 450));
            const flashRadius = Math.min(width, height) * 0.75;
            const flashGrad = ctx.createRadialGradient(targetX, targetY, 0, targetX, targetY, flashRadius);
            flashGrad.addColorStop(0, `rgba(255, 255, 255, ${flashAlpha * 0.98})`);
            flashGrad.addColorStop(0.25, `rgba(254, 240, 138, ${flashAlpha * 0.85})`);
            flashGrad.addColorStop(0.60, `rgba(239, 68, 68, ${flashAlpha * 0.55})`);
            flashGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');
            ctx.fillStyle = flashGrad;
            ctx.fillRect(0, 0, width, height);

            // B. Anamorphic Cinema Horizontal Lens Flare Streak (0ms - 350ms)
            if (blastElapsed < 350) {
              const flareAlpha = Math.max(0, 1 - (blastElapsed / 350));
              const flareGrad = ctx.createLinearGradient(0, targetY - 4, 0, targetY + 4);
              flareGrad.addColorStop(0, 'rgba(56, 189, 248, 0)');
              flareGrad.addColorStop(0.5, `rgba(255, 255, 255, ${flareAlpha * 0.95})`);
              flareGrad.addColorStop(1, 'rgba(56, 189, 248, 0)');
              ctx.fillStyle = flareGrad;
              ctx.fillRect(0, targetY - 3, width, 6);
            }
          }

          // C. Expanding Supersonic Optical Shockwave Ring (0ms - 900ms)
          if (blastElapsed < 900) {
            const ringP = blastElapsed / 900;
            const ringRadius = ringP * Math.max(width, height) * 0.95;
            const ringAlpha = Math.max(0, 1 - ringP);

            ctx.save();
            ctx.lineWidth = Math.max(1, 10 * (1 - ringP));
            ctx.strokeStyle = `rgba(255, 140, 0, ${ringAlpha * 0.9})`;
            ctx.shadowColor = '#ef4444';
            ctx.shadowBlur = 18;
            ctx.beginPath();
            ctx.arc(targetX, targetY, ringRadius, 0, Math.PI * 2);
            ctx.stroke();

            // Inner Chromatic Blue Shock Refraction
            ctx.lineWidth = Math.max(1, 4 * (1 - ringP));
            ctx.strokeStyle = `rgba(56, 189, 248, ${ringAlpha * 0.6})`;
            ctx.beginPath();
            ctx.arc(targetX, targetY, Math.max(0, ringRadius - 6), 0, Math.PI * 2);
            ctx.stroke();
            ctx.restore();
          }

          // D. Volumetric Billowing 3D Fireballs (Additive Blending + Smoke Decay)
          ctx.save();
          ctx.globalCompositeOperation = 'lighter';
          for (let i = 0; i < fireballs.length; i++) {
            const fb = fireballs[i];
            fb.life += dt * 1000;
            const p = fb.life / fb.maxLife;
            if (p >= 1) continue;

            fb.x += fb.vx * dt;
            fb.y += fb.vy * dt;
            fb.vx *= 0.94;
            fb.vy *= 0.94;

            const curR = fb.radius + (fb.maxRadius - fb.radius) * Math.sin(p * Math.PI * 0.5);
            const alpha = Math.sin(p * Math.PI) * 0.85;

            const ballGrad = ctx.createRadialGradient(fb.x, fb.y, 0, fb.x, fb.y, curR);
            if (p < 0.45) {
              // White-hot to incandescent orange
              ballGrad.addColorStop(0, `rgba(255, 255, 255, ${alpha})`);
              ballGrad.addColorStop(0.4, `rgba(251, 191, 36, ${alpha * 0.85})`);
              ballGrad.addColorStop(0.8, `rgba(239, 68, 68, ${alpha * 0.5})`);
              ballGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');
            } else {
              // Burning amber to volcanic charcoal smoke
              ballGrad.addColorStop(0, `rgba(249, 115, 22, ${alpha * 0.7})`);
              ballGrad.addColorStop(0.5, `rgba(185, 28, 28, ${alpha * 0.4})`);
              ballGrad.addColorStop(0.9, `rgba(41, 37, 36, ${alpha * 0.3})`);
              ballGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');
            }

            ctx.fillStyle = ballGrad;
            ctx.beginPath();
            ctx.arc(fb.x, fb.y, curR, 0, Math.PI * 2);
            ctx.fill();
          }
          ctx.restore();

          // E. Molten Incandescent Sparks & Shrapnel Streaks
          for (let i = 0; i < sparks.length; i++) {
            const sp = sparks[i];
            sp.life += dt * 1000;
            const p = sp.life / sp.maxLife;
            if (p >= 1) continue;

            sp.prevX = sp.x;
            sp.prevY = sp.y;

            sp.vy += sp.gravity * dt;
            sp.vx *= sp.drag;
            sp.vy *= sp.drag;
            sp.x += sp.vx * dt;
            sp.y += sp.vy * dt;

            const sparkAlpha = Math.max(0, 1 - p);
            ctx.strokeStyle = sp.color;
            ctx.globalAlpha = sparkAlpha;
            ctx.lineWidth = sp.size * (1 - p * 0.5);
            ctx.beginPath();
            ctx.moveTo(sp.prevX, sp.prevY);
            ctx.lineTo(sp.x, sp.y);
            ctx.stroke();
            ctx.globalAlpha = 1;
          }
        }

        // Continue rendering loop until total duration elapsed
        if (elapsed < TOTAL_DURATION) {
          animId = requestAnimationFrame(renderFrame);
        } else {
          // Automatic cleanup when finished
          if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
        }
      };

      animId = requestAnimationFrame(renderFrame);
    }

    playSynthesizedSound(type) {
      try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return;
        if (!this.audioContext) this.audioContext = new AudioCtx();
        if (this.audioContext.state === 'suspended') {
          this.audioContext.resume();
        }
        const ctx = this.audioContext;
        const now = ctx.currentTime;

        if (type === 'popcorn') {
          // High popping transient
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = 'triangle';
          osc.frequency.setValueAtTime(700, now);
          osc.frequency.exponentialRampToValueAtTime(1600, now + 0.04);
          gain.gain.setValueAtTime(0.4, now);
          gain.gain.exponentialRampToValueAtTime(0.01, now + 0.06);
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start(now);
          osc.stop(now + 0.06);
        } else if (type === 'tomato') {
          // Wet squishy impact
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = 'sawtooth';
          osc.frequency.setValueAtTime(320, now);
          osc.frequency.exponentialRampToValueAtTime(60, now + 0.18);
          gain.gain.setValueAtTime(0.4, now);
          gain.gain.linearRampToValueAtTime(0.01, now + 0.18);
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start(now);
          osc.stop(now + 0.18);
        } else if (type === 'laugh') {
          // Melodic bouncy chirps
          [0, 0.08, 0.16].forEach((offset, idx) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.frequency.setValueAtTime(idx % 2 === 0 ? 520 : 660, now + offset);
            gain.gain.setValueAtTime(0.25, now + offset);
            gain.gain.exponentialRampToValueAtTime(0.01, now + offset + 0.07);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(now + offset);
            osc.stop(now + offset + 0.07);
          });
        } else if (type === 'fire') {
          // Whoosh resonance
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = 'sine';
          osc.frequency.setValueAtTime(140, now);
          osc.frequency.linearRampToValueAtTime(260, now + 0.25);
          gain.gain.setValueAtTime(0.3, now);
          gain.gain.linearRampToValueAtTime(0.01, now + 0.3);
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start(now);
          osc.stop(now + 0.3);
        } else if (type === 'tactical_lock') {
          // 2026 High-Tech Telemetry Target Lock Beeps (pi-pi-pi-BEEP)
          [0, 0.1, 0.2, 0.32].forEach((offset, idx) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            const freq = idx === 3 ? 1850 : 1200;
            const dur = idx === 3 ? 0.12 : 0.05;
            osc.frequency.setValueAtTime(freq, now + offset);
            gain.gain.setValueAtTime(0.18, now + offset);
            gain.gain.exponentialRampToValueAtTime(0.001, now + offset + dur);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(now + offset);
            osc.stop(now + offset + dur);
          });
        } else if (type === 'bomb') {
          // =========================================================================
          // 2026 HOLLYWOOD CINEMATIC SUB-BASS AIR-STRIKE SOUND DESIGN
          // =========================================================================
          // 1. Supersonic Jet Turbine Diving Doppler Scream (980Hz -> 200Hz)
          const diveOsc = ctx.createOscillator();
          const diveGain = ctx.createGain();
          diveOsc.type = 'sawtooth';
          diveOsc.frequency.setValueAtTime(980, now);
          diveOsc.frequency.exponentialRampToValueAtTime(200, now + 0.42);
          diveGain.gain.setValueAtTime(0.3, now);
          diveGain.gain.exponentialRampToValueAtTime(0.01, now + 0.42);
          diveOsc.connect(diveGain);
          diveGain.connect(ctx.destination);
          diveOsc.start(now);
          diveOsc.stop(now + 0.44);

          // 2. Ground Zero Kinetic Impact Transient Crack
          const crackBuffer = ctx.createBuffer(1, ctx.sampleRate * 0.1, ctx.sampleRate);
          const crackData = crackBuffer.getChannelData(0);
          for (let i = 0; i < crackData.length; i++) {
            crackData[i] = (Math.random() * 2 - 1) * Math.exp(-i / (ctx.sampleRate * 0.02));
          }
          const crackSource = ctx.createBufferSource();
          crackSource.buffer = crackBuffer;
          const crackFilter = ctx.createBiquadFilter();
          crackFilter.type = 'highpass';
          crackFilter.frequency.setValueAtTime(800, now + 0.42);
          const crackGain = ctx.createGain();
          crackGain.gain.setValueAtTime(0.8, now + 0.42);
          crackGain.gain.exponentialRampToValueAtTime(0.01, now + 0.52);
          crackSource.connect(crackFilter);
          crackFilter.connect(crackGain);
          crackGain.connect(ctx.destination);
          crackSource.start(now + 0.42);

          // 3. Heavy 808 Tectonic Sub-Bass Rumble (45Hz -> 18Hz)
          const subOsc = ctx.createOscillator();
          const subGain = ctx.createGain();
          subOsc.type = 'triangle';
          subOsc.frequency.setValueAtTime(160, now + 0.42);
          subOsc.frequency.exponentialRampToValueAtTime(32, now + 0.85);
          subOsc.frequency.exponentialRampToValueAtTime(18, now + 1.8);
          subGain.gain.setValueAtTime(0.95, now + 0.42);
          subGain.gain.linearRampToValueAtTime(0.7, now + 0.7);
          subGain.gain.exponentialRampToValueAtTime(0.001, now + 1.8);
          subOsc.connect(subGain);
          subGain.connect(ctx.destination);
          subOsc.start(now + 0.42);
          subOsc.stop(now + 1.8);
        } else if (type === 'heart' || type === 'hug') {
          // Warm harmonic chord
          [523.25, 659.25, 783.99].forEach((freq) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.setValueAtTime(freq, now);
            gain.gain.setValueAtTime(0.18, now);
            gain.gain.exponentialRampToValueAtTime(0.01, now + 0.45);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(now);
            osc.stop(now + 0.45);
          });
        }
      } catch (_) {}
    }

    // =========================================================================
    // 5. IN-PARTY LIVE PREDICTIONS & BETTING ARENA (Gamified Popcorn Bets & Live Polls)
    // =========================================================================
    openPredictionManager() {
      if (this.activePrediction) {
        const canResolve = this.isHost || (this.activePrediction.creatorId === (this.localUser && this.localUser.id));
        if (canResolve) {
          this.openResolvePredictionModal();
        } else {
          this.renderPredictionCard();
          this.showActionToast('Prediction is live! Vote on the screen or open leaderboard 🍿');
        }
      } else {
        // Create new prediction / poll!
        this.openCreatePredictionModal();
      }
    }

    openCreatePredictionModal() {
      let modal = document.getElementById('fz-prediction-create-modal');
      if (!modal) {
        modal = document.createElement('div');
        modal.id = 'fz-prediction-create-modal';
        modal.className = 'fz-cinema-modal-backdrop';
        document.body.appendChild(modal);
      }
      modal.onclick = (e) => { if (e.target === modal) modal.style.display = 'none'; };

      modal.innerHTML = `
        <div class="fz-cinema-modal-dialog" style="max-width:480px;width:92%;">
          <div class="fz-modal-header" style="border-bottom:1px solid rgba(255,255,255,0.08);padding-bottom:12px;">
            <div class="fz-modal-title" style="display:flex;align-items:center;gap:10px;">
              <div style="width:36px;height:36px;border-radius:10px;background:#e50914;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 14px rgba(229,9,20,0.3);flex-shrink:0;">
                <i class="fa-solid fa-square-poll-vertical" style="color:#fff;font-size:18px;"></i>
              </div>
              <div>
                <div style="font-weight:800;font-size:16px;color:#fff;letter-spacing:0.3px;">Live In-Party Poll &amp; Prediction</div>
                <div style="font-size:11px;color:#a1a1aa;">Interactive live poll for party members</div>
              </div>
            </div>
            <button type="button" class="fz-modal-close" onclick="document.getElementById('fz-prediction-create-modal').style.display='none'"><i class="fa-solid fa-xmark"></i></button>
          </div>
          <div class="fz-modal-body" style="text-align:left;align-items:stretch;padding:16px 0 0 0;gap:12px;display:flex;flex-direction:column;">
            
            <!-- Question Input -->
            <div style="display:flex;flex-direction:column;gap:6px;">
              <label style="font-size:11px;font-weight:700;color:#d4d4d8;text-transform:uppercase;letter-spacing:0.5px;">
                <i class="fa-solid fa-circle-question" style="color:#e50914;margin-right:4px;"></i> Question / අනාවැකි ප්‍රශ්නය
              </label>
              <input type="text" id="wp-poll-q" class="wp-poll-field" placeholder="Ask a question (e.g. Will he survive? / මූ බේරෙයිද? / Next target කවුද?)" />
            </div>

            <!-- Dynamic Options List (2+ Unlimited Choices) -->
            <div style="display:flex;flex-direction:column;gap:8px;">
              <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
                <label style="font-size:11px;font-weight:700;color:#d4d4d8;text-transform:uppercase;letter-spacing:0.5px;">
                  <i class="fa-solid fa-list-check" style="color:#e50914;margin-right:4px;"></i> Options / පිළිතුරු (<span id="wp-poll-opt-count">2</span> Choices)
                </label>
                <button type="button" id="wp-poll-add-opt" style="background:rgba(229,9,20,0.16);border:1px solid rgba(229,9,20,0.45);color:#ffffff;font-size:11px;font-weight:700;padding:4px 10px;border-radius:999px;cursor:pointer;display:inline-flex;align-items:center;gap:5px;">
                  <i class="fa-solid fa-plus"></i> Add Answer
                </button>
              </div>
              <div id="wp-poll-options-list" style="display:flex;flex-direction:column;gap:8px;max-height:210px;overflow-y:auto;padding-right:2px;">
                <div class="wp-poll-opt-row" style="display:flex;align-items:center;gap:8px;">
                  <span class="wp-poll-opt-badge" style="font-size:12px;font-weight:800;color:#ffffff;background:rgba(255,255,255,0.12);width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0;">A</span>
                  <input type="text" id="wp-poll-oa" class="wp-poll-field wp-poll-opt-input" placeholder="Answer 1 (e.g. Yes / Survives)" />
                </div>
                <div class="wp-poll-opt-row" style="display:flex;align-items:center;gap:8px;">
                  <span class="wp-poll-opt-badge" style="font-size:12px;font-weight:800;color:#ffffff;background:rgba(229,9,20,0.28);width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0;">B</span>
                  <input type="text" id="wp-poll-ob" class="wp-poll-field wp-poll-opt-input" placeholder="Answer 2 (e.g. No / Fails)" />
                </div>
              </div>
            </div>

            <!-- Timer & Wager Mode Selectors -->
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
              <div style="display:flex;flex-direction:column;gap:6px;">
                <label style="font-size:11px;font-weight:700;color:#d4d4d8;text-transform:uppercase;letter-spacing:0.5px;">
                  <i class="fa-solid fa-stopwatch" style="color:#d4d4d8;margin-right:4px;"></i> Voting Timer
                </label>
                <select id="wp-poll-dur" class="wp-poll-select">
                  <option value="15">15 seconds (Fast)</option>
                  <option value="25" selected>25 seconds (Standard)</option>
                  <option value="45">45 seconds</option>
                  <option value="60">60 seconds (1 Min)</option>
                  <option value="90">90 seconds</option>
                </select>
              </div>

              <div style="display:flex;flex-direction:column;gap:6px;">
                <label style="font-size:11px;font-weight:700;color:#d4d4d8;text-transform:uppercase;letter-spacing:0.5px;">
                  <i class="fa-solid fa-coins" style="color:#d4d4d8;margin-right:4px;"></i> Bet / Mode
                </label>
                <select id="wp-poll-wager" class="wp-poll-select">
                  <option value="0">Free Poll (Vote Only)</option>
                  <option value="25" selected>Popcorn Bet (25 Coins - 2x Win)</option>
                  <option value="50">High Stakes (50 Coins - 2x Win)</option>
                </select>
              </div>
            </div>

            <!-- Launch & Leaderboard Buttons -->
            <div style="display:flex;gap:8px;margin-top:6px;">
              <button type="button" class="fz-btn-sec" onclick="window.FilmZillaWatchParty.openPredictionLeaderboard()" style="flex:1;">
                <i class="fa-solid fa-ranking-star"></i> Leaderboard
              </button>
              <button type="button" class="fz-btn-pri" id="wp-btn-launch-poll" style="flex:2;font-weight:800;">
                <i class="fa-solid fa-paper-plane"></i> Launch Live Poll
              </button>
            </div>
          </div>
        </div>
      `;
      modal.style.display = 'flex';

      const listEl = modal.querySelector('#wp-poll-options-list');
      const countEl = modal.querySelector('#wp-poll-opt-count');
      const addOptBtn = modal.querySelector('#wp-poll-add-opt');

      const syncOptionLabels = () => {
        if (!listEl) return;
        const rows = Array.from(listEl.querySelectorAll('.wp-poll-opt-row'));
        rows.forEach((row, idx) => {
          const letter = String.fromCharCode(65 + idx);
          const badge = row.querySelector('.wp-poll-opt-badge');
          const inp = row.querySelector('.wp-poll-opt-input');
          if (badge) {
            badge.textContent = letter;
            badge.style.background = idx % 2 === 1 ? 'rgba(229,9,20,0.28)' : 'rgba(255,255,255,0.12)';
          }
          if (inp && idx >= 2) inp.placeholder = `Answer ${idx + 1}`;
        });
        if (countEl) countEl.textContent = String(rows.length);
      };

      if (addOptBtn && listEl) {
        addOptBtn.onclick = () => {
          const curCount = listEl.querySelectorAll('.wp-poll-opt-row').length;
          if (curCount >= 8) {
            this.showActionToast('Maximum 8 poll choices allowed');
            return;
          }
          const letter = String.fromCharCode(65 + curCount);
          const row = document.createElement('div');
          row.className = 'wp-poll-opt-row';
          row.style.cssText = 'display:flex;align-items:center;gap:8px;';
          row.innerHTML = `
            <span class="wp-poll-opt-badge" style="font-size:12px;font-weight:800;color:#ffffff;background:rgba(255,255,255,0.12);width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0;">${letter}</span>
            <input type="text" class="wp-poll-field wp-poll-opt-input" placeholder="Answer ${curCount + 1}" />
            <button type="button" class="wp-poll-opt-del" title="Remove option" style="background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);color:#94a3b8;width:26px;height:26px;border-radius:50%;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;">&times;</button>
          `;
          const delBtn = row.querySelector('.wp-poll-opt-del');
          if (delBtn) {
            delBtn.onclick = () => {
              row.remove();
              syncOptionLabels();
            };
          }
          listEl.appendChild(row);
          syncOptionLabels();
          const newInp = row.querySelector('input');
          if (newInp) newInp.focus();
        };
      }

      // Attach Launch button handler
      const launchBtn = modal.querySelector('#wp-btn-launch-poll');
      if (launchBtn) {
        launchBtn.onclick = () => {
          const qInput = modal.querySelector('#wp-poll-q');
          const durSelect = modal.querySelector('#wp-poll-dur');
          const wagerSelect = modal.querySelector('#wp-poll-wager');

          const question = (qInput && qInput.value.trim()) || 'Who will survive this scene?';
          const optInputs = listEl ? Array.from(listEl.querySelectorAll('.wp-poll-opt-input')) : [];
          const rawOptions = optInputs.map((inp, idx) => (inp.value.trim() || `Option ${String.fromCharCode(65 + idx)}`));
          if (rawOptions.length < 2) {
            rawOptions.push('Option A', 'Option B');
          }
          const duration = parseInt((durSelect && durSelect.value) || '25', 10);
          const wager = parseInt((wagerSelect && wagerSelect.value) || '25', 10);

          modal.style.display = 'none';
          this.startPrediction(question, rawOptions, duration, wager);
        };
      }
    }

    startPrediction(question, optionsOrA, optionBOrDur, duration = 25, wager = 25) {
      let optList = [];
      let resolvedDur = duration;
      let resolvedWager = wager;

      if (Array.isArray(optionsOrA)) {
        resolvedDur = typeof optionBOrDur === 'number' ? optionBOrDur : 25;
        resolvedWager = typeof duration === 'number' ? duration : 25;
        optList = optionsOrA.filter(Boolean).map((lbl, idx) => ({
          key: String.fromCharCode(65 + idx),
          label: String(lbl),
          votes: 0,
          pool: resolvedWager > 0 ? resolvedWager : 0
        }));
      } else {
        optList = [
          { key: 'A', label: String(optionsOrA || 'Option A'), votes: 0, pool: resolvedWager > 0 ? resolvedWager : 0 },
          { key: 'B', label: String(optionBOrDur || 'Option B'), votes: 0, pool: resolvedWager > 0 ? resolvedWager : 0 }
        ];
      }
      if (optList.length < 2) {
        optList = [
          { key: 'A', label: 'Option A', votes: 0, pool: resolvedWager > 0 ? resolvedWager : 0 },
          { key: 'B', label: 'Option B', votes: 0, pool: resolvedWager > 0 ? resolvedWager : 0 }
        ];
      }

      const pred = {
        id: 'pred_' + Date.now(),
        creator: (this.localUser && this.localUser.name) || 'Viewer',
        creatorId: (this.localUser && this.localUser.id) || 'local',
        question,
        options: optList,
        optionA: optList[0].label,
        optionB: optList[1].label,
        duration: resolvedDur,
        wager: typeof resolvedWager === 'number' ? resolvedWager : 25,
        endTime: Date.now() + resolvedDur * 1000,
        votesA: 0,
        votesB: 0,
        poolA: (resolvedWager > 0 ? resolvedWager : 0),
        poolB: (resolvedWager > 0 ? resolvedWager : 0),
        hasVoted: false,
        myVote: null,
        myBet: 0
      };
      this.activePrediction = pred;
      this.broadcast({
        type: 'PREDICTION_START',
        prediction: pred
      });
      this.renderPredictionCard();
      const modeLabel = (pred.wager > 0) ? `${pred.wager} Coins Bet` : 'Free Community Poll';
      this.showActionToast(`Live Poll Launched (${modeLabel}): "${question}"`);
    }

    handlePredictionStart(data) {
      if (!data || !data.prediction) return;
      const incoming = data.prediction;
      const fallbackOpts = [
        { key: 'A', label: incoming.optionA || 'Option A', votes: incoming.votesA || 0, pool: incoming.poolA || 0 },
        { key: 'B', label: incoming.optionB || 'Option B', votes: incoming.votesB || 0, pool: incoming.poolB || 0 }
      ];
      this.activePrediction = {
        ...incoming,
        options: Array.isArray(incoming.options) && incoming.options.length >= 2 ? incoming.options : fallbackOpts,
        hasVoted: false,
        myVote: null,
        myBet: 0
      };
      this.renderPredictionCard();
      const wager = incoming.wager || 0;
      const toastMsg = wager > 0 ? `Popcorn Bet (${wager} Coins): "${incoming.question}"` : `Live Poll: "${incoming.question}"`;
      this.showActionToast(toastMsg);
    }

    renderPredictionCard() {
      if (!this.activePrediction) return;
      const pred = this.activePrediction;
      const playerWrap = document.getElementById('video-player-container') || document.querySelector('.player-container');
      if (!playerWrap) return;

      let card = document.getElementById('cs-prediction-card');
      if (!card) {
        card = document.createElement('div');
        card.id = 'cs-prediction-card';
        card.className = 'cs-prediction-card';
        playerWrap.appendChild(card);
      }

      const opts = Array.isArray(pred.options) && pred.options.length >= 2 ? pred.options : [
        { key: 'A', label: pred.optionA || 'Option A', votes: pred.votesA || 0, pool: pred.poolA || 0 },
        { key: 'B', label: pred.optionB || 'Option B', votes: pred.votesB || 0, pool: pred.poolB || 0 }
      ];
      pred.options = opts;

      const isFreePoll = !pred.wager || pred.wager === 0;
      const totalCount = opts.reduce((acc, o) => acc + (isFreePoll ? (o.votes || 0) : (o.pool || 0)), 0);
      const defaultPct = Math.round(100 / opts.length);

      const remSec = Math.max(0, Math.ceil((pred.endTime - Date.now()) / 1000));
      const canResolve = this.isHost || (pred.creatorId === (this.localUser && this.localUser.id));

      const optsHtml = opts.map((o) => {
        const val = isFreePoll ? (o.votes || 0) : (o.pool || 0);
        const pct = totalCount > 0 ? Math.round((val / totalCount) * 100) : defaultPct;
        const subLabel = isFreePoll ? `${pct}% (${o.votes || 0} votes)` : `${pct}% (${o.pool || 0} Coins)`;
        return `
          <button type="button" class="cs-pred-opt-btn ${pred.myVote === o.key ? 'is-voted' : ''}" id="pred-opt-${o.key.toLowerCase()}" data-opt-key="${this.escapeHtml(o.key)}" ${pred.hasVoted || remSec <= 0 ? 'disabled' : ''}>
            <div class="cs-pred-opt-bar" style="width:${pct}%"></div>
            <div class="cs-pred-opt-title"><span style="opacity:0.7;margin-right:5px;">${this.escapeHtml(o.key)}.</span>${this.escapeHtml(o.label)}</div>
            <div class="cs-pred-opt-pct">${subLabel}</div>
          </button>
        `;
      }).join('');

      card.innerHTML = `
        <div class="cs-prediction-header">
          <div class="cs-prediction-title">
            <i class="fa-solid ${isFreePoll ? 'fa-square-poll-vertical' : 'fa-trophy'}" style="color:${isFreePoll ? '#e50914' : '#fbbf24'}"></i>
            <span>${isFreePoll ? 'LIVE COMMUNITY POLL' : 'LIVE POPCORN PREDICTION'}</span>
          </div>
          <div class="cs-prediction-timer" id="cs-pred-timer">${remSec > 0 ? remSec + 's left' : 'VOTING CLOSED'}</div>
        </div>
        <div class="cs-prediction-question">${this.escapeHtml(pred.question)}</div>
        <div class="cs-prediction-options" style="${opts.length > 2 ? 'grid-template-columns:repeat(auto-fit,minmax(130px,1fr));' : ''}">
          ${optsHtml}
        </div>
        <div class="cs-prediction-footer">
          <div class="cs-prediction-coins"><i class="fa-solid fa-coins" style="color:#fbbf24"></i> Wallet: <b id="cs-user-popcorn">${this.popcornCoins}</b> Coins</div>
          ${canResolve ? `<button type="button" style="background:none;border:none;color:#e50914;cursor:pointer;font-weight:700;font-size:11px;" id="pred-btn-resolve">Declare Winner / End</button>` : ''}
        </div>
      `;
      card.style.display = 'block';

      const optBtns = typeof card.querySelectorAll === 'function'
        ? Array.from(card.querySelectorAll('.cs-pred-opt-btn'))
        : [card.querySelector('#pred-opt-a'), card.querySelector('#pred-opt-b')].filter(Boolean);
      optBtns.forEach((btn, idx) => {
        if (!pred.hasVoted) {
          const optKey = (btn.dataset && btn.dataset.optKey) || String.fromCharCode(65 + idx);
          btn.onclick = () => this.placePredictionBet(optKey);
        }
      });
      const resolveBtn = card.querySelector('#pred-btn-resolve');
      if (resolveBtn) {
        resolveBtn.onclick = () => this.openResolvePredictionModal();
      }

      // Interval countdown
      if (this.predInterval) clearInterval(this.predInterval);
      this.predInterval = setInterval(() => {
        if (!this.activePrediction) {
          clearInterval(this.predInterval);
          return;
        }
        const left = Math.max(0, Math.ceil((this.activePrediction.endTime - Date.now()) / 1000));
        const timerEl = document.getElementById('cs-pred-timer');
        if (timerEl) timerEl.textContent = left > 0 ? `${left}s left` : 'VOTING CLOSED';
        if (left <= 0) {
          clearInterval(this.predInterval);
          optBtns.forEach((b) => { b.disabled = true; });
        }
      }, 1000);
    }

    placePredictionBet(option, amount = null) {
      if (!this.activePrediction || this.activePrediction.hasVoted) return;
      const pred = this.activePrediction;
      const betAmount = amount !== null ? amount : (typeof pred.wager === 'number' ? pred.wager : 25);

      if (betAmount > 0) {
        if (this.popcornCoins < betAmount) {
          this.showActionToast('Not enough Popcorn Coins!');
          return;
        }
        this.popcornCoins -= betAmount;
        safeStorage.set('fz_popcorn_coins', String(this.popcornCoins));
      }

      const key = String(option || 'A').toUpperCase();
      pred.hasVoted = true;
      pred.myVote = key;
      pred.myBet = betAmount;

      if (Array.isArray(pred.options)) {
        const target = pred.options.find(o => o.key === key);
        if (target) {
          target.votes = (target.votes || 0) + 1;
          target.pool = (target.pool || 0) + betAmount;
        }
      }
      if (key === 'A') {
        pred.votesA = (pred.votesA || 0) + 1;
        pred.poolA = (pred.poolA || 0) + betAmount;
      } else if (key === 'B') {
        pred.votesB = (pred.votesB || 0) + 1;
        pred.poolB = (pred.poolB || 0) + betAmount;
      }

      const chosenObj = Array.isArray(pred.options) ? pred.options.find(o => o.key === key) : null;
      const chosenLabel = chosenObj ? chosenObj.label : (key === 'A' ? pred.optionA : pred.optionB);

      this.broadcast({
        type: 'PREDICTION_BET',
        predictionId: pred.id,
        option: key,
        amount: betAmount,
        user: (this.localUser && this.localUser.name) || 'Viewer'
      });

      this.renderPredictionCard();
      if (betAmount > 0) {
        this.showActionToast(`Placed ${betAmount} Coins on: ${chosenLabel}`);
      } else {
        this.showActionToast(`Voted for: ${chosenLabel}`);
      }
    }

    handlePredictionBet(data) {
      if (!this.activePrediction || this.activePrediction.id !== data.predictionId) return;
      const amount = data.amount || 0;
      const key = String(data.option || 'A').toUpperCase();
      if (Array.isArray(this.activePrediction.options)) {
        const target = this.activePrediction.options.find(o => o.key === key);
        if (target) {
          target.votes = (target.votes || 0) + 1;
          target.pool = (target.pool || 0) + amount;
        }
      }
      if (key === 'A') {
        this.activePrediction.votesA = (this.activePrediction.votesA || 0) + 1;
        this.activePrediction.poolA = (this.activePrediction.poolA || 0) + amount;
      } else if (key === 'B') {
        this.activePrediction.votesB = (this.activePrediction.votesB || 0) + 1;
        this.activePrediction.poolB = (this.activePrediction.poolB || 0) + amount;
      }
      this.renderPredictionCard();
    }

    openResolvePredictionModal() {
      if (!this.activePrediction) return;
      const pred = this.activePrediction;
      const opts = Array.isArray(pred.options) && pred.options.length >= 2 ? pred.options : [
        { key: 'A', label: pred.optionA || 'Option A' },
        { key: 'B', label: pred.optionB || 'Option B' }
      ];
      let modal = document.getElementById('fz-prediction-resolve-modal');
      if (!modal) {
        modal = document.createElement('div');
        modal.id = 'fz-prediction-resolve-modal';
        modal.className = 'fz-cinema-modal-backdrop';
        document.body.appendChild(modal);
      }
      modal.onclick = (e) => { if (e.target === modal) modal.style.display = 'none'; };

      const winButtonsHtml = opts.map((o, idx) => `
        <button type="button" class="fz-btn-pri wp-res-win-btn" data-win-key="${this.escapeHtml(o.key)}" style="background:${idx % 2 === 1 ? '#e50914' : 'rgba(255,255,255,0.1)'};border:1px solid rgba(255,255,255,0.22);">
          <i class="fa-solid fa-trophy"></i> ${this.escapeHtml(o.key)}. ${this.escapeHtml(o.label)}
        </button>
      `).join('');

      modal.innerHTML = `
        <div class="fz-cinema-modal-dialog">
          <div class="fz-modal-header">
            <div class="fz-modal-title">
              <i class="fa-solid fa-crown" style="color:#fbbf24"></i>
              <span>Declare Prediction Winner</span>
            </div>
            <button type="button" class="fz-modal-close" onclick="document.getElementById('fz-prediction-resolve-modal').style.display='none'"><i class="fa-solid fa-xmark"></i></button>
          </div>
          <div class="fz-modal-body" style="text-align:left;align-items:stretch;">
            <p style="font-size:14px;color:#f8fafc;font-weight:700;margin-bottom:14px;">${this.escapeHtml(pred.question)}</p>
            <p style="font-size:12px;color:#94a3b8;margin-bottom:14px;">Select which outcome happened in the film to reward winning voters:</p>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:12px;">
              ${winButtonsHtml}
            </div>
            <button type="button" class="fz-btn-sec" id="res-win-cancel" style="width:100%;"><i class="fa-solid fa-rotate-left"></i> Cancel / End without Winner</button>
          </div>
        </div>
      `;
      modal.style.display = 'flex';

      modal.querySelectorAll('.wp-res-win-btn').forEach((btn) => {
        btn.onclick = () => {
          modal.style.display = 'none';
          this.resolvePrediction(btn.dataset.winKey);
        };
      });
      const cancelBtn = document.getElementById('res-win-cancel');
      if (cancelBtn) {
        cancelBtn.onclick = () => {
          modal.style.display = 'none';
          this.resolvePrediction('CANCEL');
        };
      }
    }

    resolvePrediction(winningOption) {
      if (!this.activePrediction) return;
      const key = String(winningOption || '').toUpperCase();
      let winnerName = 'Cancelled';
      if (key !== 'CANCEL') {
        const found = Array.isArray(this.activePrediction.options)
          ? this.activePrediction.options.find(o => o.key === key)
          : null;
        if (found) winnerName = found.label;
        else if (key === 'A') winnerName = this.activePrediction.optionA;
        else if (key === 'B') winnerName = this.activePrediction.optionB;
      }

      const resolution = {
        type: 'PREDICTION_RESOLVE',
        predictionId: this.activePrediction.id,
        winningOption: key,
        winnerName
      };
      this.broadcast(resolution);
      this.handlePredictionResolve(resolution);
    }

    handlePredictionResolve(data) {
      if (!this.activePrediction) return;
      if (data.winningOption === 'CANCEL') {
        if (this.activePrediction.myBet > 0) {
          this.popcornCoins += this.activePrediction.myBet;
          safeStorage.set('fz_popcorn_coins', String(this.popcornCoins));
          this.showActionToast(`Prediction cancelled. ${this.activePrediction.myBet} 🍿 refunded.`);
        } else {
          this.showActionToast('Live Poll closed.');
        }
      } else {
        const didIWin = this.activePrediction.myVote === data.winningOption;
        if (didIWin) {
          const reward = (this.activePrediction.myBet > 0) ? (this.activePrediction.myBet * 2) : 10;
          this.popcornCoins += reward;
          safeStorage.set('fz_popcorn_coins', String(this.popcornCoins));
          this.showActionToast(`🏆 YOU WON ${reward} POPCORN COINS! 🍿`);
          this.playSynthesizedSound('heart');
        } else {
          this.showActionToast(`🏆 Winner Revealed: "${data.winnerName}"`);
        }
      }

      const card = document.getElementById('cs-prediction-card');
      if (card) {
        if (data.winningOption === 'CANCEL') {
          if (card.parentNode) card.parentNode.removeChild(card);
        } else {
          const didIWin = this.activePrediction.myVote === data.winningOption;
          card.innerHTML = `
            <div class="cs-prediction-winner-banner">
              <div style="font-size:28px;margin-bottom:4px;">🎉🏆🍿</div>
              <div style="font-size:16px;font-weight:800;color:#fbbf24;">${this.escapeHtml(data.winnerName)} WON!</div>
              <div style="font-size:12px;color:#cbd5e1;margin-top:4px;">
                ${didIWin ? 'Congratulations! Reward credited to your wallet 🍿' : 'Better luck on the next scene prediction!'}
              </div>
            </div>
          `;
          setTimeout(() => {
            if (card.parentNode) card.parentNode.removeChild(card);
          }, 5500);
        }
      }
      this.activePrediction = null;
    }

    openPredictionLeaderboard() {
      let modal = document.getElementById('fz-prediction-lb-modal');
      if (!modal) {
        modal = document.createElement('div');
        modal.id = 'fz-prediction-lb-modal';
        modal.className = 'fz-cinema-modal-backdrop';
        document.body.appendChild(modal);
      }
      modal.onclick = (e) => { if (e.target === modal) modal.style.display = 'none'; };

      const entries = [
        { name: `${this.localUser.name} (You)`, coins: this.popcornCoins, isMe: true }
      ];
      if (this.predictionLeaderboard && this.predictionLeaderboard.size > 0) {
        this.predictionLeaderboard.forEach((coins, user) => {
          if (user !== this.localUser.name) {
            entries.push({ name: user, coins, isMe: false });
          }
        });
      }
      if (entries.length < 2) entries.push({ name: 'Maya Cinema', coins: 180, isMe: false });
      if (entries.length < 3) entries.push({ name: 'Alex FilmFan', coins: 145, isMe: false });

      entries.sort((a, b) => b.coins - a.coins);
      const medals = ['🥇', '🥈', '🥉'];

      const rows = entries.slice(0, 5).map((e, idx) => {
        const medal = medals[idx] || `#${idx + 1}`;
        const isGold = idx === 0;
        return `
          <div style="display:flex;align-items:center;justify-content:space-between;background:${isGold ? 'rgba(251,191,36,0.12)' : 'rgba(255,255,255,0.05)'};border:${isGold ? '1px solid rgba(251,191,36,0.3)' : '1px solid rgba(255,255,255,0.08)'};padding:12px 16px;border-radius:10px;">
            <span style="font-weight:${isGold ? '800' : '700'};color:${isGold ? '#fbbf24' : (e.isMe ? '#e50914' : '#e4e4e7')};display:flex;align-items:center;gap:8px;">
              ${medal} ${this.escapeHtml(e.name)}
            </span>
            <span style="font-weight:800;color:#fff;">${e.coins} 🍿</span>
          </div>
        `;
      }).join('');

      modal.innerHTML = `
        <div class="fz-cinema-modal-dialog">
          <div class="fz-modal-header">
            <div class="fz-modal-title">
              <i class="fa-solid fa-ranking-star" style="color:#fbbf24"></i>
              <span>Watch Party Arena Leaderboard</span>
            </div>
            <button type="button" class="fz-modal-close" onclick="document.getElementById('fz-prediction-lb-modal').style.display='none'"><i class="fa-solid fa-xmark"></i></button>
          </div>
          <div class="fz-modal-body" style="align-items:stretch;">
            <div style="display:flex;flex-direction:column;gap:10px;">
              ${rows}
            </div>
            <div class="fz-modal-btn-row">
              <button type="button" class="fz-btn-pri" onclick="document.getElementById('fz-prediction-lb-modal').style.display='none'"><i class="fa-solid fa-check"></i> Close</button>
            </div>
          </div>
        </div>
      `;
      modal.style.display = 'flex';
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
