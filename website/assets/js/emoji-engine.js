/**
 * FilmZilla Apple HD Cinema Emoji Engine
 * Renders high-resolution glossy Apple iOS emoji assets across the site,
 * replacing coarse OS fonts and cartoon clipart with crisp studio visuals.
 */
(function(window) {
  'use strict';

  const APPLE_CDN = 'https://cdn.jsdelivr.net/npm/emoji-datasource-apple@15.1.2/img/apple/64';

  const FLUENT_MAP = {
    // Handshakes & Gestures (Apple HD)
    '🤝': `${APPLE_CDN}/1f91d.png`,
    '👍': `${APPLE_CDN}/1f44d.png`,
    '👎': `${APPLE_CDN}/1f44e.png`,
    '👏': `${APPLE_CDN}/1f44f.png`,
    '🙌': `${APPLE_CDN}/1f64c.png`,
    '✌️': `${APPLE_CDN}/270c-fe0f.png`,
    '🤙': `${APPLE_CDN}/1f919.png`,
    '🙏': `${APPLE_CDN}/1f64f.png`,
    '👋': `${APPLE_CDN}/1f44b.png`,
    '👌': `${APPLE_CDN}/1f44c.png`,

    // Expressive Faces (Apple HD)
    '😀': `${APPLE_CDN}/1f600.png`,
    '😂': `${APPLE_CDN}/1f602.png`,
    '🤣': `${APPLE_CDN}/1f923.png`,
    '😍': `${APPLE_CDN}/1f60d.png`,
    '🤩': `${APPLE_CDN}/1f929.png`,
    '😎': `${APPLE_CDN}/1f60e.png`,
    '🥳': `${APPLE_CDN}/1f973.png`,
    '🥺': `${APPLE_CDN}/1f97a.png`,
    '😭': `${APPLE_CDN}/1f62d.png`,
    '😱': `${APPLE_CDN}/1f631.png`,
    '🤯': `${APPLE_CDN}/1f92f.png`,
    '😴': `${APPLE_CDN}/1f634.png`,
    '💤': `${APPLE_CDN}/1f4a4.png`,
    '🤫': `${APPLE_CDN}/1f92b.png`,
    '👀': `${APPLE_CDN}/1f440.png`,
    '🤔': `${APPLE_CDN}/1f914.png`,
    '😡': `${APPLE_CDN}/1f621.png`,

    // Cinema, Entertainment & Food (Apple HD)
    '🍿': `${APPLE_CDN}/1f37f.png`,
    '🎬': `${APPLE_CDN}/1f3ac.png`,
    '🎟️': `${APPLE_CDN}/1f39f-fe0f.png`,
    '🥤': `${APPLE_CDN}/1f964.png`,
    '🍕': `${APPLE_CDN}/1f355.png`,
    '🍻': `${APPLE_CDN}/1f37b.png`,
    '🏆': `${APPLE_CDN}/1f3c6.png`,
    '🎲': `${APPLE_CDN}/1f3b2.png`,

    // Hearts & Hype (Apple HD)
    '🔥': `${APPLE_CDN}/1f525.png`,
    '❤️': `${APPLE_CDN}/2764-fe0f.png`,
    '❤': `${APPLE_CDN}/2764-fe0f.png`,
    '💔': `${APPLE_CDN}/1f494.png`,
    '💖': `${APPLE_CDN}/1f496.png`,
    '⚡': `${APPLE_CDN}/26a1.png`,
    '💯': `${APPLE_CDN}/1f4af.png`,
    '👑': `${APPLE_CDN}/1f451.png`,
    '💀': `${APPLE_CDN}/1f480.png`,
    '✨': `${APPLE_CDN}/2728.png`,
    '🎉': `${APPLE_CDN}/1f389.png`,
    '🚀': `${APPLE_CDN}/1f680.png`,
    '💣': `${APPLE_CDN}/1f4a3.png`,
    '💥': `${APPLE_CDN}/1f4a5.png`
  };

  // Unicode Regex for standard and compound emojis
  const EMOJI_REGEX = /\p{Extended_Pictographic}(\uFE0F|\u200D\p{Extended_Pictographic})*/gu;

  const FilmZillaEmoji = {
    FLUENT_MAP,

    getEmojiUrl(emoji) {
      if (!emoji) return null;
      if (FLUENT_MAP[emoji]) return FLUENT_MAP[emoji];
      const clean = emoji.replace(/\uFE0F/g, '');
      if (FLUENT_MAP[clean]) return FLUENT_MAP[clean];

      try {
        const code = [...clean].map(c => c.codePointAt(0).toString(16)).join('-');
        if (code) {
          return `${APPLE_CDN}/${code}.png`;
        }
      } catch (_) {}
      return null;
    },

    isOnlyEmojis(text) {
      if (!text) return false;
      const stripped = text.replace(EMOJI_REGEX, '').replace(/\s+/g, '');
      return stripped.length === 0 && EMOJI_REGEX.test(text);
    },

    parse(rawText) {
      if (!rawText || typeof rawText !== 'string') return rawText || '';
      return rawText.replace(EMOJI_REGEX, (match) => {
        const url = this.getEmojiUrl(match);
        if (url) {
          const clean = match.replace(/\uFE0F/g, '');
          const twCode = [...clean].map(c => c.codePointAt(0).toString(16)).join('-');
          const fb = twCode ? `https://cdnjs.cloudflare.com/ajax/libs/twemoji/14.0.2/svg/${twCode}.svg` : '';
          const errAttr = fb ? ` onerror="this.onerror=null;this.src='${fb}';"` : '';
          return `<img class="fz-inline-emoji" src="${url}" alt="${match}"${errAttr} draggable="false" loading="lazy">`;
        }
        return match;
      });
    },

    enhanceContainer(rootElement) {
      if (!rootElement) return;
      const walker = document.createTreeWalker(
        rootElement,
        NodeFilter.SHOW_TEXT,
        {
          acceptNode(node) {
            if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
            const parent = node.parentElement;
            if (!parent) return NodeFilter.FILTER_REJECT;
            if (parent.closest('script, style, input, textarea, code, pre, select, option, .fz-inline-emoji, .fz-emoji-text-span, .no-emoji-replace')) {
              return NodeFilter.FILTER_REJECT;
            }
            return EMOJI_REGEX.test(node.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
          }
        }
      );

      const nodesToReplace = [];
      let current;
      while ((current = walker.nextNode())) {
        nodesToReplace.push(current);
      }

      for (const node of nodesToReplace) {
        const parent = node.parentNode;
        if (!parent) continue;
        const html = this.parse(node.nodeValue);
        const span = document.createElement('span');
        span.className = 'fz-emoji-text-span';
        span.innerHTML = html;
        parent.replaceChild(span, node);
      }
    }
  };

  const STYLE_ID = 'fz-emoji-engine-styles';
  function injectStyles() {
    if (typeof document === 'undefined') return;
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      .fz-inline-emoji {
        width: 1.35em;
        height: 1.35em;
        object-fit: contain;
        vertical-align: -0.22em;
        display: inline-block;
        margin: 0 0.08em;
        filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.45));
        user-select: none;
        pointer-events: none;
      }
      .fz-emoji-text-span {
        display: inline;
      }
      .jumbo-emojis {
        display: inline-flex;
        flex-wrap: wrap;
        gap: 6px;
        align-items: center;
        padding: 3px 0;
      }
      .jumbo-emojis .fz-inline-emoji {
        width: 32px;
        height: 32px;
        margin: 0;
        filter: drop-shadow(0 4px 10px rgba(0, 0, 0, 0.65));
        animation: jumboEmojiPulse 0.25s cubic-bezier(0.175, 0.885, 0.32, 1.275);
      }
      @keyframes jumboEmojiPulse {
        0% { transform: scale(0.6); opacity: 0; }
        100% { transform: scale(1); opacity: 1; }
      }
    `;
    (document.head || document.documentElement).appendChild(style);
  }

  // Auto-enhance document body on load
  if (typeof document !== 'undefined') {
    injectStyles();
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => {
        injectStyles();
        FilmZillaEmoji.enhanceContainer(document.body);
      });
    } else {
      setTimeout(() => {
        injectStyles();
        FilmZillaEmoji.enhanceContainer(document.body);
      }, 150);
    }
  }

  window.FilmZillaEmoji = FilmZillaEmoji;

})(window);
