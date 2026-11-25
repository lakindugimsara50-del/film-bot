/**
 * FilmZilla – Unified User State & Personal Cinema Memory Engine (v1.0)
 * Handles Google One Tap Identity, Continue Watching, History, Favorites,
 * Watch Later, Genre Affinity Vectors, and Event-Driven UI Sync.
 * Zero-server, 100% Client-Side Privacy, LocalStorage Quota Protected.
 */

'use strict';

(function(window) {
  const STORAGE_KEYS = {
    PROFILE: 'fz_user_profile',
    CONTINUE_WATCHING: 'fz_continue_watching',
    WATCH_HISTORY: 'fz_watch_history',
    WATCH_LATER: 'fz_watch_later',
    FAVORITES: 'fz_favorites',
    AFFINITY: 'fz_genre_affinity',
    RADAR: 'fz_release_radar',
    SETTINGS: 'fz_user_settings'
  };

  const MAX_HISTORY_ITEMS = 80;
  const MAX_CONTINUE_ITEMS = 25;

  // Safe LocalStorage helpers with automatic JSON & quota recovery
  function _get(key, defaultValue) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : defaultValue;
    } catch (e) {
      console.warn(`[FilmZilla State] Failed to read ${key}:`, e);
      return defaultValue;
    }
  }

  function _set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch (e) {
      // Quota exceeded: trim history and try once more
      if (e && (e.name === 'QuotaExceededError' || e.code === 22)) {
        console.warn(`[FilmZilla State] Quota exceeded on ${key}, trimming history...`);
        _trimStorage();
        try {
          localStorage.setItem(key, JSON.stringify(value));
          return true;
        } catch (err) {
          console.error(`[FilmZilla State] Critical storage write failure:`, err);
        }
      }
      return false;
    }
  }

  function _trimStorage() {
    try {
      const hist = _get(STORAGE_KEYS.WATCH_HISTORY, []);
      if (hist.length > 20) {
        _set(STORAGE_KEYS.WATCH_HISTORY, hist.slice(0, 20));
      }
    } catch (_) {}
  }

  function _emit(eventName, detail = {}) {
    try {
      window.dispatchEvent(new CustomEvent(eventName, { detail }));
      window.dispatchEvent(new CustomEvent('fz:state-changed', { detail: { event: eventName, ...detail } }));
    } catch (_) {}
  }

  // Generate sleek Obsidian/Crimson studio monogram avatar for guest accounts
  function _createGuestAvatar(name) {
    const initials = (name || 'G').slice(0, 2).toUpperCase();
    const bgColors = ['#e50914', '#b91c1c', '#18181b', '#27272a', '#991b1b', '#141418'];
    let hash = 0;
    for (let i = 0; i < initials.length; i++) hash += initials.charCodeAt(i);
    const bg = bgColors[Math.abs(hash) % bgColors.length];
    return `data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80" viewBox="0 0 80 80"><rect width="80" height="80" rx="40" fill="${encodeURIComponent(bg)}" stroke="%23e50914" stroke-width="2"/><text x="50%" y="54%" font-family="Arial,sans-serif" font-weight="bold" font-size="30" fill="%23ffffff" dominant-baseline="middle" text-anchor="middle">${initials}</text></svg>`;
  }

  // ---------------------------------------------------------------------
  // Profile & Identity Engine (Google One Tap + Guest Mode)
  // ---------------------------------------------------------------------
  const Profile = {
    get() {
      let profile = _get(STORAGE_KEYS.PROFILE, null);
      if (!profile) {
        // Create an initial default guest profile
        const randId = Math.floor(100 + Math.random() * 900);
        profile = {
          isGuest: true,
          id: `guest_${Date.now()}`,
          name: `Guest #${randId}`,
          given_name: `Guest #${randId}`,
          email: '',
          picture: _createGuestAvatar(`G${randId}`),
          avatar: _createGuestAvatar(`G${randId}`),
          createdAt: Date.now()
        };
        _set(STORAGE_KEYS.PROFILE, profile);
      }
      if (profile && profile.isGuest && typeof profile.picture === 'string' && profile.picture.includes('url(%23g)')) {
        const healed = _createGuestAvatar(profile.name || 'G');
        profile.picture = healed;
        profile.avatar = healed;
        _set(STORAGE_KEYS.PROFILE, profile);
      }
      if (profile && !profile.avatar && profile.picture) {
        profile.avatar = profile.picture;
      }
      return profile;
    },

    setGoogleUser(credentialToken) {
      try {
        // Decode JWT payload (standard Base64URL)
        const parts = credentialToken.split('.');
        if (parts.length < 2) return null;
        const payloadBase64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
        const jsonPayload = decodeURIComponent(atob(payloadBase64).split('').map(c => {
          return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
        }).join(''));
        const data = JSON.parse(jsonPayload);

        const profile = {
          isGuest: false,
          id: data.sub || `google_${Date.now()}`,
          name: data.name || 'Google User',
          given_name: data.given_name || (data.name ? data.name.split(' ')[0] : 'User'),
          email: data.email || '',
          picture: data.picture || _createGuestAvatar(data.name || 'U'),
          googleSub: data.sub,
          signedInAt: Date.now()
        };

        _set(STORAGE_KEYS.PROFILE, profile);
        _emit('fz:profile-updated', { profile });
        return profile;
      } catch (err) {
        console.error('[FilmZilla State] Error decoding Google token:', err);
        return null;
      }
    },

    setPicture(newPicture) {
      if (!newPicture) return;
      const profile = this.get();
      profile.picture = newPicture;
      profile.avatar = newPicture;
      profile.customAvatar = true;
      _set(STORAGE_KEYS.PROFILE, profile);
      _emit('fz:profile-updated', { profile });
      return profile;
    },

    setCustomName(newName) {
      if (!newName || !newName.trim()) return;
      const profile = this.get();
      profile.name = newName.trim();
      profile.given_name = profile.name.split(' ')[0];
      if (profile.isGuest && !profile.customAvatar) {
        profile.picture = _createGuestAvatar(profile.given_name);
        profile.avatar = profile.picture;
      }
      _set(STORAGE_KEYS.PROFILE, profile);
      _emit('fz:profile-updated', { profile });
      return profile;
    },

    signOut() {
      const randId = Math.floor(100 + Math.random() * 900);
      const guest = {
        isGuest: true,
        id: `guest_${Date.now()}`,
        name: `Guest #${randId}`,
        given_name: `Guest #${randId}`,
        email: '',
        picture: _createGuestAvatar(`G${randId}`),
        createdAt: Date.now()
      };
      _set(STORAGE_KEYS.PROFILE, guest);
      _emit('fz:profile-updated', { profile: guest });
      return guest;
    }
  };

  // ---------------------------------------------------------------------
  // Favorites Engine
  // ---------------------------------------------------------------------
  const Favorites = {
    getAll() {
      return _get(STORAGE_KEYS.FAVORITES, []);
    },

    has(movieId) {
      if (!movieId) return false;
      const list = this.getAll();
      return list.some(item => (item.id === movieId || item.slug === movieId));
    },

    toggle(movie) {
      if (!movie || (!movie.id && !movie.slug)) return false;
      const id = movie.id || movie.slug;
      let list = this.getAll();
      const existingIdx = list.findIndex(m => m.id === id || m.slug === id);
      let isAdded = false;

      if (existingIdx >= 0) {
        list.splice(existingIdx, 1);
        isAdded = false;
      } else {
        const item = {
          id: id,
          slug: movie.slug || id,
          title: movie.title || 'Untitled',
          title_si: movie.title_si || '',
          year: movie.year || '',
          rating: movie.rating || movie.imdb || '0.0',
          poster: movie.poster || movie.poster_url || '',
          backdrop: movie.backdrop || movie.backdrop_url || '',
          genres: Array.isArray(movie.genres) ? movie.genres : [],
          type: movie.type || 'movie',
          addedAt: Date.now()
        };
        list.unshift(item);
        isAdded = true;
        Affinity.record(movie.genres, 5); // +5 affinity for favoriting
      }

      _set(STORAGE_KEYS.FAVORITES, list);
      _emit('fz:favorites-changed', { movieId: id, isAdded, count: list.length, list });
      return isAdded;
    },

    count() {
      return this.getAll().length;
    },

    remove(movieId) {
      if (!movieId) return;
      let list = this.getAll().filter(item => item.id !== movieId && item.slug !== movieId);
      _set(STORAGE_KEYS.FAVORITES, list);
      _emit('fz:favorites-changed', { movieId, isAdded: false, count: list.length, list });
    },

    clear() {
      _set(STORAGE_KEYS.FAVORITES, []);
      _emit('fz:favorites-changed', { count: 0, list: [] });
    }
  };

  // ---------------------------------------------------------------------
  // Watch Later Queue
  // ---------------------------------------------------------------------
  const WatchLater = {
    getAll() {
      return _get(STORAGE_KEYS.WATCH_LATER, []);
    },

    has(movieId) {
      if (!movieId) return false;
      const list = this.getAll();
      return list.some(item => (item.id === movieId || item.slug === movieId));
    },

    toggle(movie) {
      if (!movie || (!movie.id && !movie.slug)) return false;
      const id = movie.id || movie.slug;
      let list = this.getAll();
      const existingIdx = list.findIndex(m => m.id === id || m.slug === id);
      let isAdded = false;

      if (existingIdx >= 0) {
        list.splice(existingIdx, 1);
        isAdded = false;
      } else {
        const item = {
          id: id,
          slug: movie.slug || id,
          title: movie.title || 'Untitled',
          title_si: movie.title_si || '',
          year: movie.year || '',
          rating: movie.rating || movie.imdb || '0.0',
          poster: movie.poster || movie.poster_url || '',
          backdrop: movie.backdrop || movie.backdrop_url || '',
          genres: Array.isArray(movie.genres) ? movie.genres : [],
          type: movie.type || 'movie',
          addedAt: Date.now()
        };
        list.unshift(item);
        isAdded = true;
        Affinity.record(movie.genres, 3); // +3 affinity for watch later
      }

      _set(STORAGE_KEYS.WATCH_LATER, list);
      _emit('fz:watchlater-changed', { movieId: id, isAdded, count: list.length, list });
      return isAdded;
    },

    count() {
      return this.getAll().length;
    },

    remove(movieId) {
      if (!movieId) return;
      let list = this.getAll().filter(item => item.id !== movieId && item.slug !== movieId);
      _set(STORAGE_KEYS.WATCH_LATER, list);
      _emit('fz:watchlater-changed', { movieId, isAdded: false, count: list.length, list });
    },

    clear() {
      _set(STORAGE_KEYS.WATCH_LATER, []);
      _emit('fz:watchlater-changed', { count: 0, list: [] });
    }
  };

  // ---------------------------------------------------------------------
  // Continue Watching & Auto-Resume
  // ---------------------------------------------------------------------
  const ContinueWatching = {
    getAll() {
      const map = _get(STORAGE_KEYS.CONTINUE_WATCHING, {});
      const list = Object.values(map);
      list.sort((a, b) => (b.lastWatched || 0) - (a.lastWatched || 0));
      return list.slice(0, MAX_CONTINUE_ITEMS);
    },

    getProgress(movieId) {
      if (!movieId) return null;
      const map = _get(STORAGE_KEYS.CONTINUE_WATCHING, {});
      return map[movieId] || null;
    },

    saveProgress(movie, currentTime, duration, extra = {}) {
      if (!movie || (!movie.id && !movie.slug)) return;
      if (!currentTime || currentTime < 10) return; // Don't track if < 10 seconds

      const id = movie.id || movie.slug;
      const dur = duration && duration > 0 ? duration : (movie.durationSec || 7200);
      const pct = Math.min(100, Math.round((currentTime / dur) * 1000) / 10);

      const map = _get(STORAGE_KEYS.CONTINUE_WATCHING, {});

      // If user finished >94% of the movie, remove from Continue Watching and add to History
      if (pct >= 94) {
        if (map[id]) {
          delete map[id];
          _set(STORAGE_KEYS.CONTINUE_WATCHING, map);
        }
        WatchHistory.add(movie, currentTime, dur, true);
        _emit('fz:continue-changed', { movieId: id, removed: true, list: Object.values(map) });
        return;
      }

      const item = {
        id: id,
        slug: movie.slug || id,
        title: movie.title || 'Untitled',
        title_si: movie.title_si || '',
        year: movie.year || '',
        rating: movie.rating || movie.imdb || '0.0',
        poster: movie.poster || movie.poster_url || '',
        backdrop: movie.backdrop || movie.backdrop_url || '',
        currentTime: Math.round(currentTime),
        duration: Math.round(dur),
        percentage: pct,
        season: extra.season || movie.season || null,
        episode: extra.episode || movie.episode || null,
        episode_title: extra.episode_title || movie.episode_title || '',
        lastWatched: Date.now()
      };

      map[id] = item;
      _set(STORAGE_KEYS.CONTINUE_WATCHING, map);

      // Record slight affinity and mirror in history
      Affinity.record(movie.genres, 1);
      WatchHistory.add(movie, currentTime, dur, false);

      _emit('fz:continue-changed', { movieId: id, item, list: Object.values(map) });
    },

    remove(movieId) {
      if (!movieId) return;
      const map = _get(STORAGE_KEYS.CONTINUE_WATCHING, {});
      if (map[movieId]) {
        delete map[movieId];
        _set(STORAGE_KEYS.CONTINUE_WATCHING, map);
        _emit('fz:continue-changed', { movieId, removed: true, list: Object.values(map) });
      }
    },

    count() {
      return this.getAll().length;
    },

    clear() {
      _set(STORAGE_KEYS.CONTINUE_WATCHING, {});
      _emit('fz:continue-changed', { list: [] });
    }
  };

  // ---------------------------------------------------------------------
  // Watch History
  // ---------------------------------------------------------------------
  const WatchHistory = {
    getAll() {
      return _get(STORAGE_KEYS.WATCH_HISTORY, []);
    },

    add(movie, currentTime, duration, isCompleted = false) {
      if (!movie || (!movie.id && !movie.slug)) return;
      const id = movie.id || movie.slug;
      let list = this.getAll();

      // Remove existing item to bump it to the very top
      list = list.filter(item => item.id !== id && item.slug !== id);

      const entry = {
        id: id,
        slug: movie.slug || id,
        title: movie.title || 'Untitled',
        title_si: movie.title_si || '',
        year: movie.year || '',
        rating: movie.rating || movie.imdb || '0.0',
        poster: movie.poster || movie.poster_url || '',
        backdrop: movie.backdrop || movie.backdrop_url || '',
        currentTime: Math.round(currentTime || 0),
        duration: Math.round(duration || 0),
        isCompleted: Boolean(isCompleted),
        genres: Array.isArray(movie.genres) ? movie.genres : [],
        watchedAt: Date.now(),
        timestamp: Date.now()
      };

      list.unshift(entry);
      if (list.length > MAX_HISTORY_ITEMS) {
        list = list.slice(0, MAX_HISTORY_ITEMS);
      }

      _set(STORAGE_KEYS.WATCH_HISTORY, list);
      _emit('fz:history-changed', { list });
    },

    remove(movieId) {
      if (!movieId) return;
      let list = this.getAll().filter(item => item.id !== movieId && item.slug !== movieId);
      _set(STORAGE_KEYS.WATCH_HISTORY, list);
      _emit('fz:history-changed', { list });
    },

    count() {
      return this.getAll().length;
    },

    clear() {
      _set(STORAGE_KEYS.WATCH_HISTORY, []);
      _emit('fz:history-changed', { list: [] });
    }
  };

  // ---------------------------------------------------------------------
  // Genre Affinity Scoring (Client-Side "For You" ML Engine)
  // ---------------------------------------------------------------------
  const Affinity = {
    getScores() {
      return _get(STORAGE_KEYS.AFFINITY, {});
    },

    record(genres, weight = 1) {
      if (!genres || !Array.isArray(genres) || genres.length === 0) return;
      const scores = this.getScores();
      genres.forEach(g => {
        if (!g) return;
        const key = String(g).trim().toLowerCase();
        scores[key] = (scores[key] || 0) + weight;
      });
      _set(STORAGE_KEYS.AFFINITY, scores);
    },

    getTopGenres(topN = 3) {
      const scores = this.getScores();
      const entries = Object.entries(scores);
      if (entries.length === 0) return [];
      entries.sort((a, b) => b[1] - a[1]);
      return entries.slice(0, topN).map(e => e[0]);
    }
  };

  // ---------------------------------------------------------------------
  // Release Radar (Alert when CAM upgrades to 1080p or Sinhala Sub added)
  // ---------------------------------------------------------------------
  const Radar = {
    getAll() {
      return _get(STORAGE_KEYS.RADAR, []);
    },

    has(movieId) {
      return this.getAll().some(item => item.id === movieId || item.slug === movieId);
    },

    toggle(movie) {
      if (!movie) return false;
      const id = movie.id || movie.slug;
      let list = this.getAll();
      const idx = list.findIndex(item => item.id === id || item.slug === id);
      let isEnrolled = false;

      if (idx >= 0) {
        list.splice(idx, 1);
        isEnrolled = false;
      } else {
        list.unshift({
          id: id,
          slug: movie.slug || id,
          title: movie.title || 'Untitled',
          currentQuality: movie.quality || 'CAM',
          hasSinhalaSub: Boolean(movie.has_sinhala_sub),
          enrolledAt: Date.now()
        });
        isEnrolled = true;
      }

      _set(STORAGE_KEYS.RADAR, list);
      _emit('fz:radar-changed', { movieId: id, isEnrolled, list });
      return isEnrolled;
    },

    // Check movie updates and notify
    checkUpdates(allMoviesList) {
      if (!Array.isArray(allMoviesList) || allMoviesList.length === 0) return [];
      const enrolled = this.getAll();
      if (enrolled.length === 0) return [];

      const triggered = [];
      const remaining = [];

      enrolled.forEach(sub => {
        const found = allMoviesList.find(m => m.id === sub.id || m.slug === sub.slug);
        if (found) {
          const q = (found.quality || '').toLowerCase();
          const qualityUpgraded = (sub.currentQuality.toLowerCase().includes('cam') || sub.currentQuality === '480p') &&
                                  (q.includes('1080p') || q.includes('720p') || q.includes('hd') || q.includes('web-dl') || q.includes('bluray'));
          const subAdded = !sub.hasSinhalaSub && Boolean(found.has_sinhala_sub);

          if (qualityUpgraded || subAdded) {
            triggered.push({
              movie: found,
              qualityUpgraded,
              subAdded,
              message: qualityUpgraded 
                ? `${found.title} is now available in ${found.quality}!` 
                : `${found.title} now has Sinhala Subtitles!`
            });
          } else {
            remaining.push(sub);
          }
        } else {
          remaining.push(sub);
        }
      });

      if (triggered.length > 0) {
        _set(STORAGE_KEYS.RADAR, remaining);
        _emit('fz:radar-alert', { alerts: triggered });
      }

      return triggered;
    }
  };

  // Expose as global FilmZilla User API
  Favorites.getCount = () => Favorites.count();
  WatchLater.getCount = () => WatchLater.count();
  ContinueWatching.getCount = () => ContinueWatching.count();
  WatchHistory.getCount = () => WatchHistory.count();
  Affinity.get = () => Affinity.getScores();
  Radar.track = (movie) => Radar.toggle(movie);
  Radar.checkUpgrade = (movie) => {
    const res = Radar.checkUpdates([movie]);
    return { upgraded: res.length > 0, details: res[0] || null };
  };

  window.FilmSubUser = window.FZUser = {
    Profile,
    Favorites,
    WatchLater,
    ContinueWatching,
    WatchHistory,
    History: WatchHistory,
    Affinity,
    Radar,
    ReleaseRadar: Radar,
    getUser: () => Profile.get(),
    getGenreAffinity: () => Affinity.get(),
    STORAGE_KEYS
  };

})(window);
