/**
 * FilmZilla High-Definition 3D Emoji Engine
 * Renders high-quality Microsoft Fluent 3D & Twemoji vector graphics across the site,
 * replacing primitive low-res OS font emojis with modern, glossy cinema-grade visuals.
 */
(function(window) {
  'use strict';

  const FLUENT_MAP = {
    // Handshakes & Gestures
    '🤝': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Handshake/3D/handshake_3d.png',
    '👍': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Thumbs%20up/Default/3D/thumbs_up_3d_default.png',
    '👎': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Thumbs%20down/Default/3D/thumbs_down_3d_default.png',
    '👏': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Clapping%20hands/Default/3D/clapping_hands_3d_default.png',
    '🙌': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Raising%20hands/Default/3D/raising_hands_3d_default.png',
    '✌️': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Victory%20hand/Default/3D/victory_hand_3d_default.png',
    '🤙': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Call%20me%20hand/Default/3D/call_me_hand_3d_default.png',
    '🙏': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Folded%20hands/Default/3D/folded_hands_3d_default.png',
    '👋': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Waving%20hand/Default/3D/waving_hand_3d_default.png',
    '👌': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f44c/512.webp',

    // Expressive Faces
    '😀': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f600/512.webp',
    '😂': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f923/512.webp',
    '🤣': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f923/512.webp',
    '😍': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f60d/512.webp',
    '🤩': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f929/512.webp',
    '😎': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f60e/512.webp',
    '🥳': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f973/512.webp',
    '🥺': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f97a/512.webp',
    '😭': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f62d/512.webp',
    '😱': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f631/512.webp',
    '🤯': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f92f/512.webp',
    '😴': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f634/512.webp',
    '💤': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f4a4/512.webp',
    '🤫': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f92b/512.webp',
    '👀': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f440/512.webp',
    '🤔': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f914/512.webp',

    // Cinema, Entertainment & Food
    '🍿': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f37f/512.webp',
    '🎬': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f3ac/512.webp',
    '🎟️': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f39f_fe0f/512.webp',
    '🥤': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f964/512.webp',
    '🍕': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f355/512.webp',
    '🍻': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f37b/512.webp',
    '🏆': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f3c6/512.webp',
    '🎲': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f3b2/512.webp',

    // Hearts & Hype
    '🔥': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f525/512.webp',
    '❤️': 'https://fonts.gstatic.com/s/e/notoemoji/latest/2764_fe0f/512.webp',
    '❤': 'https://fonts.gstatic.com/s/e/notoemoji/latest/2764_fe0f/512.webp',
    '💔': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f494/512.webp',
    '💖': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f496/512.webp',
    '⚡': 'https://fonts.gstatic.com/s/e/notoemoji/latest/26a1/512.webp',
    '💯': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f4af/512.webp',
    '👑': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f451/512.webp',
    '💀': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f480/512.webp',
    '✨': 'https://fonts.gstatic.com/s/e/notoemoji/latest/2728/512.webp',
    '🎉': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f389/512.webp',
    '🚀': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f680/512.webp',
    '💣': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f4a3/512.webp',
    '💥': 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f4a5/512.webp'
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

      // Universal Twemoji SVG vector fallback for any other Unicode emoji
      try {
        const code = [...clean].map(c => c.codePointAt(0).toString(16)).join('-');
        if (code) {
          return `https://cdnjs.cloudflare.com/ajax/libs/twemoji/14.0.2/svg/${code}.svg`;
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
          return `<img class="fz-inline-emoji" src="${url}" alt="${match}" draggable="false" loading="lazy">`;
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
