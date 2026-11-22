/**
 * DOM and VM Sandbox Engine for FilmZilla / FilmSub E2E Tests
 * Built with zero external dependencies (Node.js built-ins: fs, path, vm, assert).
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

/**
 * Normalizes hex color string to [r, g, b] in range [0, 255]
 */
function hexToRgb(hex) {
  let clean = hex.replace('#', '').trim();
  if (clean.length === 3) {
    clean = clean.split('').map(c => c + c).join('');
  }
  const num = parseInt(clean, 16);
  if (isNaN(num)) return [0, 0, 0];
  return [(num >> 16) & 255, (num >> 8) & 255, num & 255];
}

/**
 * Calculates WCAG 2.1 relative luminance of an sRGB color.
 */
function relativeLuminance(rgb) {
  const [r, g, b] = rgb.map(val => {
    const s = val / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/**
 * Computes WCAG 2.1 contrast ratio between two hex colors.
 * Formula: (L1 + 0.05) / (L2 + 0.05) where L1 >= L2.
 */
function computeContrastRatio(hex1, hex2) {
  const lum1 = relativeLuminance(hexToRgb(hex1));
  const lum2 = relativeLuminance(hexToRgb(hex2));
  const lighter = Math.max(lum1, lum2);
  const darker = Math.min(lum1, lum2);
  return (lighter + 0.05) / (darker + 0.05);
}

/**
 * CSS Token & Media Query Analyzer Helpers
 */
const cssAnalyzer = {
  computeContrastRatio,

  parseCssVariables(cssContent) {
    const vars = {};
    const rootRegex = /:root\s*\{([^}]+)\}/g;
    let match;
    while ((match = rootRegex.exec(cssContent)) !== null) {
      const block = match[1];
      const propRegex = /(--[\w-]+)\s*:\s*([^;]+);/g;
      let propMatch;
      while ((propMatch = propRegex.exec(block)) !== null) {
        vars[propMatch[1].trim()] = propMatch[2].trim();
      }
    }
    return vars;
  },

  parseMediaQueries(cssContent) {
    const queries = [];
    const mediaRegex = /@media\s*([^{]+)\{([\s\S]*?\n\})/g;
    let match;
    while ((match = mediaRegex.exec(cssContent)) !== null) {
      queries.push({
        condition: match[1].trim(),
        content: match[2].trim()
      });
    }
    return queries;
  },

  findZIndexDeclarations(cssContent) {
    const zIndices = [];
    const ruleRegex = /([^{}]+)\{([^}]+)\}/g;
    let match;
    while ((match = ruleRegex.exec(cssContent)) !== null) {
      const selector = match[1].trim();
      const body = match[2];
      const zMatch = /z-index\s*:\s*([^;]+);/.exec(body);
      if (zMatch) {
        zIndices.push({
          selector,
          zIndex: parseInt(zMatch[1].trim(), 10) || zMatch[1].trim()
        });
      }
    }
    return zIndices;
  },

  scanNeonDropShadows(cssContent) {
    const neonGlowRegex = /box-shadow\s*:[^;]*(rgba?\(\s*229\s*,\s*9\s*,\s*20\s*,\s*(?:0\.[7-9]|1)\)|#[eE]50914)[^;]*/g;
    const matches = [];
    let match;
    while ((match = neonGlowRegex.exec(cssContent)) !== null) {
      matches.push(match[0].trim());
    }
    return matches;
  }
};

/**
 * In-memory MockElement with robust query, classList, and event support
 */
class MockElement {
  constructor(tagName = 'div', id = '', className = '') {
    this.tagName = tagName.toUpperCase();
    this.id = id;
    this.className = className;
    this.attributes = {};
    if (id) this.attributes.id = id;
    if (className) this.attributes.class = className;

    this._classes = new Set(className ? className.split(/\s+/).filter(Boolean) : []);
    const self = this;
    this.classList = {
      _classes: this._classes,
      add(...tokens) {
        tokens.forEach(t => {
          if (t) self._classes.add(t);
        });
        self.className = Array.from(self._classes).join(' ');
        self.attributes.class = self.className;
      },
      remove(...tokens) {
        tokens.forEach(t => {
          if (t) self._classes.delete(t);
        });
        self.className = Array.from(self._classes).join(' ');
        self.attributes.class = self.className;
      },
      contains(t) {
        return self._classes.has(t);
      },
      toggle(t, force) {
        if (force === true) {
          self._classes.add(t);
          self.className = Array.from(self._classes).join(' ');
          self.attributes.class = self.className;
          return true;
        } else if (force === false) {
          self._classes.delete(t);
          self.className = Array.from(self._classes).join(' ');
          self.attributes.class = self.className;
          return false;
        }
        if (self._classes.has(t)) {
          self._classes.delete(t);
          self.className = Array.from(self._classes).join(' ');
          self.attributes.class = self.className;
          return false;
        } else {
          self._classes.add(t);
          self.className = Array.from(self._classes).join(' ');
          self.attributes.class = self.className;
          return true;
        }
      },
      get length() {
        return self._classes.size;
      }
    };

    this.style = {};
    this.dataset = {};
    this.children = [];
    this.parentElement = null;
    this.parentNode = null;
    this._innerHTML = '';
    this._textContent = '';
    this.value = '';
    this.eventListeners = {};

    // Sizing and positioning mocks
    this.scrollWidth = 360;
    this.scrollHeight = 640;
    this.offsetWidth = 360;
    this.offsetHeight = 640;
    this.clientWidth = 360;
    this.clientHeight = 640;
    this.scrollTop = 0;
    this.scrollLeft = 0;
  }

  get innerHTML() {
    return this._innerHTML;
  }

  set innerHTML(html) {
    this._innerHTML = html;
    this.children = [];
    if (!html || typeof html !== 'string') return;

    // Lightweight parsing of child tags for element traversal
    const tagRegex = /<([a-zA-Z0-9-]+)([^>]*)>([\s\S]*?)<\/\1>|<([a-zA-Z0-9-]+)([^>]*)\/>/g;
    let match;
    while ((match = tagRegex.exec(html)) !== null) {
      const tag = match[1] || match[4];
      const attrsStr = match[2] || match[5] || '';
      const inner = match[3] || '';
      const child = new MockElement(tag);

      // Extract id
      const idMatch = /id=["']([^"']+)["']/.exec(attrsStr);
      if (idMatch) {
        child.id = idMatch[1];
        child.attributes.id = idMatch[1];
      }

      // Extract class
      const classMatch = /class=["']([^"']+)["']/.exec(attrsStr);
      if (classMatch) {
        child.className = classMatch[1];
        child.attributes.class = classMatch[1];
        child._classes = new Set(classMatch[1].split(/\s+/).filter(Boolean));
        child.classList._classes = child._classes;
      }

      // Extract data attributes
      const dataRegex = /data-([\w-]+)=["']([^"']+)["']/g;
      let dataMatch;
      while ((dataMatch = dataRegex.exec(attrsStr)) !== null) {
        const camelKey = dataMatch[1].replace(/-([a-z])/g, (_, g) => g.toUpperCase());
        child.dataset[camelKey] = dataMatch[2];
        child.attributes[`data-${dataMatch[1]}`] = dataMatch[2];
      }

      child.innerHTML = inner;
      child.parentElement = this;
      child.parentNode = this;
      this.children.push(child);
    }
  }

  get textContent() {
    if (this._textContent) return this._textContent;
    return this._innerHTML.replace(/<[^>]+>/g, '').trim();
  }

  set textContent(txt) {
    this._textContent = String(txt);
    this._innerHTML = String(txt).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    this.children = [];
  }

  setAttribute(name, value) {
    const val = String(value);
    this.attributes[name] = val;
    if (name === 'id') this.id = val;
    if (name === 'class') {
      this.className = val;
      this._classes = new Set(val.split(/\s+/).filter(Boolean));
      this.classList._classes = this._classes;
    }
    if (name.startsWith('data-')) {
      const camel = name.slice(5).replace(/-([a-z])/g, (_, g) => g.toUpperCase());
      this.dataset[camel] = val;
    }
    if (name === 'value') this.value = val;
    if (name === 'src') this.src = val;
    if (name === 'href') this.href = val;
  }

  getAttribute(name) {
    if (name in this.attributes) return this.attributes[name];
    if (name === 'id' && this.id) return this.id;
    if (name === 'class' && this.className) return this.className;
    if (name.startsWith('data-')) {
      const camel = name.slice(5).replace(/-([a-z])/g, (_, g) => g.toUpperCase());
      if (camel in this.dataset) return this.dataset[camel];
    }
    return null;
  }

  hasAttribute(name) {
    return this.getAttribute(name) !== null;
  }

  removeAttribute(name) {
    delete this.attributes[name];
    if (name === 'id') this.id = '';
    if (name === 'class') {
      this.className = '';
      this._classes.clear();
    }
    if (name.startsWith('data-')) {
      const camel = name.slice(5).replace(/-([a-z])/g, (_, g) => g.toUpperCase());
      delete this.dataset[camel];
    }
  }

  appendChild(child) {
    if (child) {
      child.parentElement = this;
      child.parentNode = this;
      this.children.push(child);
    }
    return child;
  }

  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index !== -1) {
      this.children.splice(index, 1);
      child.parentElement = null;
      child.parentNode = null;
    }
    return child;
  }

  insertBefore(newNode, refNode) {
    const index = this.children.indexOf(refNode);
    if (index !== -1) {
      this.children.splice(index, 0, newNode);
    } else {
      this.children.push(newNode);
    }
    newNode.parentElement = this;
    newNode.parentNode = this;
    return newNode;
  }

  remove() {
    if (this.parentElement) {
      this.parentElement.removeChild(this);
    }
  }

  addEventListener(event, handler) {
    if (!this.eventListeners[event]) this.eventListeners[event] = [];
    this.eventListeners[event].push(handler);
  }

  removeEventListener(event, handler) {
    if (!this.eventListeners[event]) return;
    this.eventListeners[event] = this.eventListeners[event].filter(h => h !== handler);
  }

  dispatchEvent(event) {
    const type = event.type || event;
    const ev = typeof event === 'object' ? event : { type, target: this, preventDefault: () => {}, stopPropagation: () => {} };
    if (!ev.target) ev.target = this;
    const handlers = this.eventListeners[type] || [];
    handlers.forEach(h => {
      try {
        h.call(this, ev);
      } catch (err) {
        if (err instanceof assert.AssertionError || (err && err.name === 'AssertionError')) {
          throw err;
        }
        console.error(`Error in event listener for ${type}:`, err);
      }
    });
    if (this['on' + type] && typeof this['on' + type] === 'function') {
      try {
        this['on' + type].call(this, ev);
      } catch (err) {
        if (err instanceof assert.AssertionError || (err && err.name === 'AssertionError')) {
          throw err;
        }
        console.error(`Error in inline event listener for on${type}:`, err);
      }
    }
    return !ev.defaultPrevented;
  }

  play() {
    this.paused = false;
    this.dispatchEvent({ type: 'play', target: this });
    return Promise.resolve();
  }

  pause() {
    this.paused = true;
    this.dispatchEvent({ type: 'pause', target: this });
  }

  click() {
    this.dispatchEvent({ type: 'click', target: this, preventDefault: () => {}, stopPropagation: () => {} });
  }

  focus() {
    this.dispatchEvent({ type: 'focus', target: this, preventDefault: () => {}, stopPropagation: () => {} });
  }

  blur() {
    this.dispatchEvent({ type: 'blur', target: this, preventDefault: () => {}, stopPropagation: () => {} });
  }

  matches(selector) {
    const sel = selector.trim();
    if (sel.startsWith('#')) {
      return this.id === sel.slice(1);
    }
    if (sel.startsWith('.')) {
      return this.classList.contains(sel.slice(1));
    }
    if (sel.startsWith('[') && sel.endsWith(']')) {
      const inside = sel.slice(1, -1);
      const eqIdx = inside.indexOf('=');
      if (eqIdx !== -1) {
        const key = inside.slice(0, eqIdx).trim();
        const val = inside.slice(eqIdx + 1).replace(/^["']|["']$/g, '').trim();
        return this.getAttribute(key) === val;
      }
      return this.hasAttribute(inside.trim());
    }
    if (/^[a-zA-Z0-9-]+$/.test(sel)) {
      return this.tagName === sel.toUpperCase();
    }
    return false;
  }

  closest(selector) {
    let curr = this;
    while (curr) {
      if (curr.matches && curr.matches(selector)) return curr;
      curr = curr.parentElement;
    }
    return null;
  }

  querySelector(selector) {
    const all = this.querySelectorAll(selector);
    return all.length > 0 ? all[0] : null;
  }

  querySelectorAll(selector) {
    const results = [];
    const sel = selector.trim();

    function traverse(node) {
      if (node.matches && node.matches(sel)) {
        results.push(node);
      }
      if (node.children) {
        for (const child of node.children) {
          traverse(child);
        }
      }
    }

    if (this.children) {
      for (const child of this.children) {
        traverse(child);
      }
    }

    // Fallback: If no parsed children matched, check innerHTML for matches
    if (results.length === 0 && this._innerHTML) {
      if (sel.startsWith('#')) {
        const id = sel.slice(1);
        if (this._innerHTML.includes(`id="${id}"`) || this._innerHTML.includes(`id='${id}'`)) {
          results.push(new MockElement('div', id));
        }
      } else if (sel.startsWith('.')) {
        const cls = sel.slice(1);
        const classRegex = new RegExp(`class=["'][^"']*\\b${cls}\\b[^"']*["']`, 'g');
        const matches = this._innerHTML.match(classRegex) || [];
        matches.forEach((_, idx) => {
          results.push(new MockElement('div', '', cls));
        });
      }
    }

    return results;
  }

  getElementById(id) {
    if (this.id === id) return this;
    if (this.children) {
      for (const child of this.children) {
        const found = child.getElementById(id);
        if (found) return found;
      }
    }
    return null;
  }

  getElementsByClassName(cls) {
    return this.querySelectorAll(`.${cls}`);
  }

  getElementsByTagName(tag) {
    return this.querySelectorAll(tag);
  }

  getBoundingClientRect() {
    return {
      top: 0,
      left: 0,
      right: this.offsetWidth || 360,
      bottom: this.offsetHeight || 640,
      width: this.offsetWidth || 360,
      height: this.offsetHeight || 640,
      x: 0,
      y: 0
    };
  }
}

/**
 * In-memory MockDocument representing the page DOM tree
 */
class MockDocument {
  constructor() {
    this.documentElement = new MockElement('html');
    this.head = new MockElement('head');
    this.body = new MockElement('body');
    this.documentElement.appendChild(this.head);
    this.documentElement.appendChild(this.body);

    this.registry = new Map();
    this.eventListeners = {};
    this.activeElement = this.body;
  }

  createElement(tag) {
    const el = new MockElement(tag);
    return el;
  }

  createElementNS(ns, tag) {
    return this.createElement(tag);
  }

  createTextNode(text) {
    const el = new MockElement('#text');
    el.textContent = text;
    return el;
  }

  createDocumentFragment() {
    return new MockElement('fragment');
  }

  registerElement(el) {
    if (el && el.id) {
      this.registry.set(el.id, el);
    }
  }

  getElementById(id) {
    if (this.registry.has(id)) {
      return this.registry.get(id);
    }
    const fromTree = this.documentElement.getElementById(id);
    if (fromTree) {
      this.registry.set(id, fromTree);
      return fromTree;
    }
    return null;
  }

  querySelector(selector) {
    if (selector.startsWith('#')) {
      const id = selector.slice(1);
      const el = this.getElementById(id);
      if (el) return el;
    }
    return this.documentElement.querySelector(selector);
  }

  querySelectorAll(selector) {
    return this.documentElement.querySelectorAll(selector);
  }

  getElementsByClassName(cls) {
    return this.documentElement.getElementsByClassName(cls);
  }

  getElementsByTagName(tag) {
    if (tag.toLowerCase() === 'body') return [this.body];
    if (tag.toLowerCase() === 'head') return [this.head];
    if (tag.toLowerCase() === 'html') return [this.documentElement];
    return this.documentElement.getElementsByTagName(tag);
  }

  addEventListener(event, handler) {
    if (!this.eventListeners[event]) this.eventListeners[event] = [];
    this.eventListeners[event].push(handler);
  }

  removeEventListener(event, handler) {
    if (!this.eventListeners[event]) return;
    this.eventListeners[event] = this.eventListeners[event].filter(h => h !== handler);
  }

  dispatchEvent(event) {
    const type = event.type || event;
    const handlers = this.eventListeners[type] || [];
    handlers.forEach(h => h(event));
    if (this['on' + type]) this['on' + type](event);
  }

  /**
   * Loads an HTML file or string and populates head, body, and element registry
   */
  loadHtml(html) {
    // Extract head
    const headMatch = /<head[^>]*>([\s\S]*?)<\/head>/i.exec(html);
    if (headMatch) {
      this.head.innerHTML = headMatch[1];
    }
    // Extract body
    const bodyMatch = /<body[^>]*>([\s\S]*?)<\/body>/i.exec(html);
    if (bodyMatch) {
      this.body.innerHTML = bodyMatch[1];
    } else {
      this.body.innerHTML = html;
    }

    // Index all elements with id in the HTML
    const idRegex = /<([a-zA-Z0-9-]+)[^>]*\bid=["']([^"']+)["'][^>]*>/g;
    let match;
    while ((match = idRegex.exec(html)) !== null) {
      const tag = match[1];
      const id = match[2];
      if (!this.registry.has(id)) {
        const el = new MockElement(tag, id);
        this.registry.set(id, el);
      }
    }
  }
}

/**
 * Mock LocalStorage with configurable quota exception support
 */
class MockStorage {
  constructor() {
    this._data = {};
    this._quotaExceeded = false;
  }

  setQuotaExceeded(val) {
    this._quotaExceeded = Boolean(val);
  }

  getItem(key) {
    return key in this._data ? this._data[key] : null;
  }

  setItem(key, value) {
    if (this._quotaExceeded) {
      const err = new Error('QuotaExceededError');
      err.name = 'QuotaExceededError';
      err.code = 22;
      throw err;
    }
    this._data[key] = String(value);
  }

  removeItem(key) {
    delete this._data[key];
  }

  clear() {
    this._data = {};
  }

  key(index) {
    const keys = Object.keys(this._data);
    return keys[index] || null;
  }

  get length() {
    return Object.keys(this._data).length;
  }
}

/**
 * Mock Video.js Player Engine (`mockVjs`)
 */
function createMockVjs(options = {}) {
  let currentTimeVal = options.currentTime || 0;
  let durationVal = options.duration || 7200;
  let pausedVal = options.paused !== undefined ? options.paused : true;
  let mutedVal = options.muted || false;
  let volumeVal = options.volume !== undefined ? options.volume : 1.0;
  let srcVal = options.src || '';
  let errorVal = null;
  const classes = new Set(['video-js', 'vjs-default-skin']);
  const listeners = {};

  const player = {
    currentTime(val) {
      if (val !== undefined) {
        currentTimeVal = Number(val);
        player.trigger('seeking');
        player.trigger('timeupdate');
        return player;
      }
      return currentTimeVal;
    },
    duration() {
      return durationVal;
    },
    paused() {
      return pausedVal;
    },
    play() {
      pausedVal = false;
      player.trigger('play');
      return Promise.resolve();
    },
    pause() {
      pausedVal = true;
      player.trigger('pause');
      return player;
    },
    muted(val) {
      if (val !== undefined) {
        mutedVal = Boolean(val);
        player.trigger('volumechange');
        return player;
      }
      return mutedVal;
    },
    volume(val) {
      if (val !== undefined) {
        volumeVal = Number(val);
        player.trigger('volumechange');
        return player;
      }
      return volumeVal;
    },
    src(val) {
      if (val !== undefined) {
        srcVal = val;
        return player;
      }
      return srcVal;
    },
    error(err) {
      if (err !== undefined) {
        errorVal = err;
        player.trigger('error');
        return player;
      }
      return errorVal;
    },
    addClass(c) {
      classes.add(c);
      return player;
    },
    removeClass(c) {
      classes.delete(c);
      return player;
    },
    hasClass(c) {
      return classes.has(c);
    },
    on(event, cb) {
      if (!listeners[event]) listeners[event] = [];
      listeners[event].push(cb);
    },
    off(event, cb) {
      if (!listeners[event]) return;
      listeners[event] = listeners[event].filter(h => h !== cb);
    },
    one(event, cb) {
      const wrapper = (data) => {
        player.off(event, wrapper);
        cb(data);
      };
      player.on(event, wrapper);
    },
    trigger(event, data) {
      const handlers = listeners[event] || [];
      handlers.forEach(h => {
        try {
          h(data);
        } catch (e) {
          if (e instanceof assert.AssertionError || (e && e.name === 'AssertionError')) {
            throw e;
          }
          console.error(`Error in mockVjs listener for ${event}:`, e);
        }
      });
    },
    el() {
      const mockEl = new MockElement('div', 'mock-vjs-container');
      mockEl.classList._classes = classes;
      return mockEl;
    },
    ready(cb) {
      if (typeof cb === 'function') cb.call(player);
      return player;
    }
  };

  return player;
}

/**
 * Creates a complete sandbox environment for running FilmZilla / FilmSub scripts inside `node:vm`
 */
function createVmSandbox(customState = {}) {
  const doc = new MockDocument();
  const localStorage = new MockStorage();
  const sessionStorage = new MockStorage();
  const vibrationLog = [];

  const sandbox = {
    window: {},
    document: doc,
    localStorage,
    sessionStorage,
    navigator: {
      vibrate: (pattern) => {
        vibrationLog.push(pattern);
        return true;
      },
      clipboard: {
        _text: '',
        writeText: async (t) => { sandbox.navigator.clipboard._text = t; },
        readText: async () => sandbox.navigator.clipboard._text
      },
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
      mediaDevices: {
        getUserMedia: async () => ({
          getTracks: () => [
            { kind: 'audio', stop: () => {}, enabled: true },
            { kind: 'video', stop: () => {}, enabled: true }
          ]
        })
      }
    },
    location: {
      href: 'https://filmsub.pages.dev/',
      origin: 'https://filmsub.pages.dev',
      protocol: 'https:',
      host: 'filmsub.pages.dev',
      pathname: '/',
      search: '',
      hash: ''
    },
    history: {
      pushState: (state, title, url) => {
        if (url) sandbox.location.href = url;
      },
      replaceState: (state, title, url) => {
        if (url) sandbox.location.href = url;
      }
    },
    IntersectionObserver: class {
      constructor(callback, options) {
        this.callback = callback;
        this.options = options;
        this.observed = [];
        sandbox._lastIntersectionObserver = this;
      }
      observe(target) { this.observed.push(target); }
      unobserve(target) { this.observed = this.observed.filter(t => t !== target); }
      disconnect() { this.observed = []; }
      trigger(entries) { this.callback(entries, this); }
    },
    fetch: typeof fetch !== 'undefined' ? fetch : (url) => Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve('')
    }),
    BroadcastChannel: class {
      constructor(name) {
        this.name = name;
        this.onmessage = null;
        this.listeners = [];
      }
      postMessage(msg) {
        if (this.onmessage) this.onmessage({ data: msg });
        this.listeners.forEach(fn => fn({ data: msg }));
      }
      addEventListener(ev, fn) {
        if (ev === 'message') this.listeners.push(fn);
      }
      close() {}
    },
    URLSearchParams: global.URLSearchParams,
    URL: global.URL,
    setTimeout: (fn, delay, ...args) => setTimeout(fn, delay, ...args),
    clearTimeout: (id) => clearTimeout(id),
    setInterval: (fn, delay, ...args) => setInterval(fn, delay, ...args),
    clearInterval: (id) => clearInterval(id),
    requestAnimationFrame: (cb) => setTimeout(() => cb(Date.now()), 16),
    cancelAnimationFrame: (id) => clearTimeout(id),
    getComputedStyle: (el) => el.style || {},
    matchMedia: (query) => ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {}
    }),
    AudioContext: class {
      constructor() {
        this.destination = {};
      }
      createGain() { return { gain: { value: 1 }, connect: () => {} }; }
      createBiquadFilter() { return { frequency: { value: 70 }, type: 'lowpass', connect: () => {} }; }
      createMediaElementSource() { return { connect: () => {} }; }
    },
    FilmSub: {
      escHtml: (str) => String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
      showToast: () => {}
    },
    console,
    _vibrationLog: vibrationLog,
    ...customState
  };

  sandbox.window = sandbox;
  sandbox.self = sandbox;
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.addEventListener = (ev, fn) => doc.addEventListener(ev, fn);
  sandbox.removeEventListener = (ev, fn) => doc.removeEventListener(ev, fn);

  vm.createContext(sandbox);
  return sandbox;
}

module.exports = {
  MockElement,
  MockDocument,
  MockStorage,
  createMockVjs,
  createVmSandbox,
  cssAnalyzer,
  computeContrastRatio
};
