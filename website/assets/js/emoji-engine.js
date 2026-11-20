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
    '😀': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Grinning%20face/3D/grinning_face_3d.png',
    '😂': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Rolling%20on%20the%20floor%20laughing/3D/rolling_on_the_floor_laughing_3d.png',
    '🤣': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Rolling%20on%20the%20floor%20laughing/3D/rolling_on_the_floor_laughing_3d.png',
    '😍': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Smiling%20face%20with%20heart-eyes/3D/smiling_face_with_heart-eyes_3d.png',
    '🤩': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Star-struck/3D/star-struck_3d.png',
    '😎': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Smiling%20face%20with%20sunglasses/3D/smiling_face_with_sunglasses_3d.png',
    '🥳': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Partying%20face/3D/partying_face_3d.png',
    '🥺': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Pleading%20face/3D/pleading_face_3d.png',
    '😭': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Loudly%20crying%20face/3D/loudly_crying_face_3d.png',
    '😱': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Face%20screaming%20in%20fear/3D/face_screaming_in_fear_3d.png',
    '🤯': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Exploding%20head/3D/exploding_head_3d.png',
    '😴': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Sleeping%20face/3D/sleeping_face_3d.png',
    '💤': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Zzz/3D/zzz_3d.png',
    '🤫': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Shushing%20face/3D/shushing_face_3d.png',
    '👀': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Eyes/3D/eyes_3d.png',
    '🤔': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Thinking%20face/3D/thinking_face_3d.png',

    // Cinema, Entertainment & Food
    '🍿': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Popcorn/3D/popcorn_3d.png',
    '🎬': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Clapper%20board/3D/clapper_board_3d.png',
    '🎟️': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Admission%20tickets/3D/admission_tickets_3d.png',
    '🥤': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Cup%20with%20straw/3D/cup_with_straw_3d.png',
    '🍕': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Pizza/3D/pizza_3d.png',
    '🍻': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Clinking%20beer%20mugs/3D/clinking_beer_mugs_3d.png',
    '🏆': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Trophy/3D/trophy_3d.png',
    '🎲': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Game%20die/3D/game_die_3d.png',

    // Hearts & Hype
    '🔥': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Fire/3D/fire_3d.png',
    '❤️': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Red%20heart/3D/red_heart_3d.png',
    '❤': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Red%20heart/3D/red_heart_3d.png',
    '💔': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Broken%20heart/3D/broken_heart_3d.png',
    '💖': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Sparkling%20heart/3D/sparkling_heart_3d.png',
    '⚡': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/High%20voltage/3D/high_voltage_3d.png',
    '💯': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Hundred%20points/3D/hundred_points_3d.png',
    '👑': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Crown/3D/crown_3d.png',
    '💀': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Skull/3D/skull_3d.png',
    '✨': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Sparkles/3D/sparkles_3d.png',
    '🎉': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Party%20popper/3D/party_popper_3d.png',
    '🚀': 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Rocket/3D/rocket_3d.png'
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
