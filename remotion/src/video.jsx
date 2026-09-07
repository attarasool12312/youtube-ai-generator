import React from 'react';

import '@fontsource/manrope/700.css';
import '@fontsource/inter/700.css';
import '@fontsource/montserrat/700.css';
import '@fontsource/poppins/700.css';
import '@fontsource/roboto/700.css';
import '@fontsource/open-sans/700.css';
import '@fontsource/lato/700.css';
import '@fontsource/nunito/700.css';
import '@fontsource/merriweather/700.css';
import '@fontsource/source-sans-3/700.css';

import {
  AbsoluteFill,
  Audio,
  Img,
  OffthreadVideo,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

import {
  TransitionSeries,
  linearTiming,
  crossZoom,
  dreamyZoom,
  linearBlur,
  pushCut,
} from '@remotion/transitions';

const clamp = {
  extrapolateLeft: 'clamp',
  extrapolateRight: 'clamp',
};

const clean = (v) =>
  String(v || '').replace(/\s+/g, ' ').trim();

const hash = (value) => {
  let h = 2166136261;
  for (const c of clean(value)) {
    h ^= c.charCodeAt(0);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h >>> 0);
};

/* ---------------------------------------------------------
   VIDEO-LEVEL STYLE SYSTEM
   Different videos deterministically receive different DNA.
--------------------------------------------------------- */

const NICHE_PALETTES = {
  nasa: [
    {
      accent: '#39D8FF',
      accent2: '#6777FF',
      soft: '#C9F6FF',
      dark: '#040A14',
    },
    {
      accent: '#64E9FF',
      accent2: '#8A6CFF',
      soft: '#D8F8FF',
      dark: '#050916',
    },
  ],

  news: [
    {
      accent: '#5BA8FF',
      accent2: '#E75A62',
      soft: '#DCEBFF',
      dark: '#08111B',
    },
    {
      accent: '#7CB8FF',
      accent2: '#D8E3F0',
      soft: '#EFF6FF',
      dark: '#09121C',
    },
  ],

  science: [
    {
      accent: '#35D5C8',
      accent2: '#3F9EFF',
      soft: '#C9FFF7',
      dark: '#041411',
    },
    {
      accent: '#46E1B7',
      accent2: '#55B8FF',
      soft: '#D5FFF4',
      dark: '#061510',
    },
  ],

  history: [
    {
      accent: '#D9A84E',
      accent2: '#B96F3A',
      soft: '#F4DFB2',
      dark: '#171007',
    },
    {
      accent: '#E6B85E',
      accent2: '#8E6846',
      soft: '#F7E6C4',
      dark: '#151008',
    },
  ],

  business: [
    {
      accent: '#56B8FF',
      accent2: '#39D39F',
      soft: '#D7F1FF',
      dark: '#06121A',
    },
    {
      accent: '#5D8FFF',
      accent2: '#47D7B1',
      soft: '#DEE7FF',
      dark: '#07111A',
    },
  ],

  generic: [
    {
      accent: '#37C9FF',
      accent2: '#7868FF',
      soft: '#C9F3FF',
      dark: '#07111D',
    },
    {
      accent: '#36D399',
      accent2: '#22A7F0',
      soft: '#C5FBE8',
      dark: '#061510',
    },
    {
      accent: '#F4B942',
      accent2: '#F97316',
      soft: '#FFE7A8',
      dark: '#151006',
    },
  ],
};

const inferNiche = (scenes) => {
  const text = clean(
    scenes
      .slice(0, 20)
      .map(
        (scene) =>
          `${scene.subject} ` +
          `${scene.environment} ` +
          `${scene.action} ` +
          `${scene.focalDetail} ` +
          `${scene.visualDistinction}`
      )
      .join(' ')
  ).toLowerCase();

  if (
    /\b(nasa|space|planet|moon|mars|rocket|orbit|astronaut|satellite|galaxy|asteroid|telescope)\b/.test(text)
  ) {
    return 'nasa';
  }

  if (
    /\b(news|government|president|minister|election|war|breaking|reporter|parliament|white house|downing street)\b/.test(text)
  ) {
    return 'news';
  }

  if (
    /\b(science|scientist|laboratory|research|discovery|experiment|microscope|physics|biology|chemistry|quantum|medical)\b/.test(text)
  ) {
    return 'science';
  }

  if (
    /\b(ancient|history|civilization|empire|roman|egypt|archaeology|ruins|temple|medieval|pharaoh|artifact)\b/.test(text)
  ) {
    return 'history';
  }

  if (
    /\b(business|money|finance|economy|market|bank|stock|investment|company|revenue|inflation|currency|credit)\b/.test(text)
  ) {
    return 'business';
  }

  return 'generic';
};

const getTheme = (scenes) => {
  const niche = inferNiche(scenes);

  const signature = scenes
    .slice(0, 12)
    .map(
      (scene) =>
        `${scene.subject}|` +
        `${scene.environment}|` +
        `${scene.visualDistinction}`
    )
    .join('|');

  const palettes =
    NICHE_PALETTES[niche] ||
    NICHE_PALETTES.generic;

  return {
    ...palettes[
      hash(signature) % palettes.length
    ],
    niche,
    seed: hash(signature),
  };
};

/* ---------------------------------------------------------
   TRANSITIONS
--------------------------------------------------------- */

const transitionFrames = (scene, index) => {
  const signature =
    `${scene.subject}|${scene.action}|${index}`;

  const choice =
    hash(signature) % 8;

  // Intentional clean hard cuts keep the edit from
  // feeling like every scene uses an effect.
  if (choice === 0 || choice === 5) {
    return 0;
  }

  return [
    22,
    26,
    30,
    24,
    28,
    32,
  ][choice % 6];
};

const transitionPresentation = (
  scene,
  index
) => {
  const signature =
    `${scene.subject}|${scene.environment}|` +
    `${scene.visualDistinction}|${index}`;

  const variants = [
    linearBlur,
    crossZoom,
    pushCut,
    linearBlur,
    dreamyZoom,
    pushCut,
    crossZoom,
  ];

  const selected =
    variants[
      hash(signature) %
      variants.length
    ];

  try {
    return selected();
  } catch {
    return linearBlur();
  }
};

/* ---------------------------------------------------------
   IMAGE MOTION
--------------------------------------------------------- */

const getMotion = (scene, index) => {
  const text = clean(
    `${scene.shotType} ${scene.cameraAngle} ` +
    `${scene.visualDistinction} ${scene.action}`
  ).toLowerCase();

  if (text.includes('macro')) return 'macro';
  if (text.includes('overhead')) return 'overhead';
  if (text.includes('aerial')) return 'aerial';
  if (text.includes('tracking')) return 'tracking';
  if (text.includes('low angle')) return 'rise';
  if (text.includes('high angle')) return 'fall';

  const presets = [
    'push',
    'pull',
    'left',
    'right',
    'rise',
    'fall',
    'float',
    'drift',
    'perspectiveLeft',
    'perspectiveRight',
    'reveal',
    'hold',
  ];

  const signature =
    `${scene.subject}|${scene.environment}|` +
    `${scene.visualDistinction}|${index}`;

  return presets[
    hash(signature) % presets.length
  ];
};

const motionValues = (preset, progress) => {
  const p =
    progress * progress * (3 - 2 * progress);

  switch (preset) {
    case 'pull':
      return {
        scale: 1.16 - p * 0.12,
        x: 0, y: 0, rotate: 0,
        rotateX: 0, rotateY: 0,
      };

    case 'left':
      return {
        scale: 1.105,
        x: 4.8 - p * 9.6,
        y: 0.5 - p,
        rotate: -0.22,
        rotateX: 0,
        rotateY: 0.7,
      };

    case 'right':
      return {
        scale: 1.105,
        x: -4.8 + p * 9.6,
        y: -0.5 + p,
        rotate: 0.22,
        rotateX: 0,
        rotateY: -0.7,
      };

    case 'rise':
      return {
        scale: 1.075 + p * 0.055,
        x: 0.4 - p * 0.8,
        y: 4.2 - p * 8.4,
        rotate: -0.14,
        rotateX: 0.5,
        rotateY: 0,
      };

    case 'fall':
      return {
        scale: 1.11 - p * 0.03,
        x: -0.4 + p * 0.8,
        y: -4 + p * 8,
        rotate: 0.12,
        rotateX: -0.5,
        rotateY: 0,
      };

    case 'macro':
      return {
        scale: 1.045 + p * 0.095,
        x: -1.3 + p * 2.6,
        y: 0.9 - p * 1.8,
        rotate: 0,
        rotateX: 0.25,
        rotateY: -0.25,
      };

    case 'overhead':
      return {
        scale: 1.10,
        x: -3 + p * 6,
        y: 1.8 - p * 3.6,
        rotate: -0.35 + p * 0.7,
        rotateX: 0,
        rotateY: 0,
      };

    case 'aerial':
      return {
        scale: 1.12 - p * 0.045,
        x: -3.5 + p * 7,
        y: 2.2 - p * 4.4,
        rotate: 0.3 - p * 0.55,
        rotateX: 0.35,
        rotateY: -0.25,
      };

    case 'tracking':
      return {
        scale: 1.075,
        x: -5 + p * 10,
        y: Math.sin(p * Math.PI) * 0.7,
        rotate: 0,
        rotateX: 0,
        rotateY: -0.45 + p * 0.9,
      };

    case 'float':
      return {
        scale: 1.075 + p * 0.03,
        x: Math.sin(p * Math.PI) * 2,
        y: -1.7 + p * 3.4,
        rotate: -0.18 + p * 0.36,
        rotateX: 0.35,
        rotateY: -0.35,
      };

    case 'drift':
      return {
        scale: 1.085 + p * 0.02,
        x: -2.8 + p * 5.6,
        y: -1.6 + p * 3.2,
        rotate: -0.12,
        rotateX: -0.3,
        rotateY: 0.4,
      };

    case 'perspectiveLeft':
      return {
        scale: 1.095 + p * 0.025,
        x: 2.7 - p * 5.4,
        y: 0,
        rotate: -0.16,
        rotateX: 0.25,
        rotateY: 1.2 - p * 2.4,
      };

    case 'perspectiveRight':
      return {
        scale: 1.095 + p * 0.025,
        x: -2.7 + p * 5.4,
        y: 0,
        rotate: 0.16,
        rotateX: -0.25,
        rotateY: -1.2 + p * 2.4,
      };

    case 'reveal':
      return {
        scale: 1.13 - p * 0.055,
        x: -1.8 + p * 3.6,
        y: 1.2 - p * 2.4,
        rotate: -0.12 + p * 0.24,
        rotateX: 0.55 - p * 0.55,
        rotateY: -0.55 + p * 0.55,
      };

    case 'hold':
      return {
        scale: 1.055 + p * 0.012,
        x: Math.sin(p * Math.PI) * 0.45,
        y: Math.sin(p * Math.PI * 0.75) * 0.35,
        rotate: 0,
        rotateX: 0,
        rotateY: 0,
      };

    default:
      return {
        scale: 1.035 + p * 0.105,
        x: 0, y: 0, rotate: 0,
        rotateX: 0, rotateY: 0,
      };
  }
};

const SceneMedia = ({
  scene,
  index,
  durationInFrames,
  theme,
}) => {
  const frame = useCurrentFrame();

  const p = interpolate(
    frame,
    [0, Math.max(1, durationInFrames - 1)],
    [0, 1],
    clamp
  );

  const preset = getMotion(scene, index);
  const m = motionValues(preset, p);

  const src = staticFile(scene.asset);

  if (scene.mediaType === 'video') {
    return (
      <AbsoluteFill
        style={{
          background: '#04070B',
          overflow: 'hidden',
        }}
      >
        <OffthreadVideo
          src={src}
          muted
          style={{
            width: '100%',
            height: '100%',
            objectFit: 'cover',
          }}
        />

        <CinematicFinish
          p={p}
          theme={theme}
          index={index}
        />
      </AbsoluteFill>
    );
  }

  const backgroundX = -m.x * 0.45;
  const backgroundY = -m.y * 0.45;

  const sweep = interpolate(
    p,
    [0, 1],
    [-45, 55],
    clamp
  );

  return (
    <AbsoluteFill
      style={{
        background: '#04070B',
        overflow: 'hidden',
      }}
    >
      {/* Background depth layer */}
      <Img
        src={src}
        style={{
          position: 'absolute',
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          transform:
            `scale(1.19) ` +
            `translate(${backgroundX}%, ${backgroundY}%)`,
          filter:
            'blur(20px) brightness(0.56) saturate(1.28)',
          opacity: 0.72,
        }}
      />

      {/* Main image layer */}
      <Img
        src={src}
        style={{
          position: 'absolute',
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          transform:
            `perspective(1800px) ` +
            `scale(${m.scale}) ` +
            `translate(${m.x}%, ${m.y}%) ` +
            `rotateX(${m.rotateX}deg) ` +
              `rotateY(${m.rotateY}deg) ` +
              `rotate(${m.rotate}deg)`,
          transformOrigin: 'center center',
          filter:
            'contrast(1.045) saturate(1.075)',
        }}
      />

      {/* Moving colored light */}
      <AbsoluteFill
        style={{
          pointerEvents: 'none',
          opacity: 0.12,
          transform: `translateX(${sweep}%)`,
          background:
            `linear-gradient(110deg, ` +
            `transparent 28%, ` +
            `${theme.accent}55 48%, ` +
            `${theme.accent2}38 56%, ` +
            `transparent 73%)`,
        }}
      />

      <CinematicFinish
        p={p}
        theme={theme}
        index={index}
      />
    </AbsoluteFill>
  );
};

const CinematicFinish = ({
  theme,
  index,
}) => (
  <>
    <AbsoluteFill
      style={{
        pointerEvents: 'none',
        background:
          'linear-gradient(to bottom,' +
          'rgba(0,0,0,.12) 0%,' +
          'transparent 26%,' +
          'transparent 67%,' +
          'rgba(0,0,0,.33) 100%)',
      }}
    />

    <AbsoluteFill
      style={{
        pointerEvents: 'none',
        boxShadow:
          'inset 0 0 170px rgba(0,0,0,.26)',
      }}
    />

    {index % 4 === 1 ? (
      <AbsoluteFill
        style={{
          pointerEvents: 'none',
          opacity: 0.06,
          background:
            `radial-gradient(circle at 78% 18%, ` +
            `${theme.accent} 0%, transparent 32%)`,
        }}
      />
    ) : null}
  </>
);

/* ---------------------------------------------------------
   FOCUS CHIP — TOP LEFT, SMALL
--------------------------------------------------------- */

const compactWords = (value, maxWords = 5) =>
  clean(value)
    .split(' ')
    .filter(Boolean)
    .slice(0, maxWords)
    .join(' ');

const editorialGraphicFor = (scene) => {
  const narration = clean(scene.narration);
  if (!narration) return null;

  const stat = narration.match(
    /(?:[$£€]\s*)?\d+(?:[.,]\d+)?\s*(?:%|percent|million|billion|trillion)\b/i
  );

  if (stat) {
    return {
      type: 'stat',
      value: stat[0],
      label: compactWords(scene.subject, 5) || 'Key figure',
    };
  }

  const years = narration.match(/\b(?:18|19|20)\d{2}\b/g) || [];

  if (years.length >= 2) {
    return {
      type: 'timeline',
      from: years[0],
      to: years[years.length - 1],
    };
  }

  if (
    /\b(vs\.?|versus|compared to|more than|less than|twice|half as)\b/i.test(
      narration
    )
  ) {
    return {
      type: 'comparison',
      left: compactWords(scene.subject, 4) || 'A',
      right:
        compactWords(
          scene.focalDetail || scene.visualDistinction,
          4
        ) || 'B',
    };
  }

  if (
    /\b(first|then|next|finally|step|process)\b/i.test(narration)
  ) {
    const items = [
      compactWords(scene.subject, 3),
      compactWords(scene.action, 3),
      compactWords(scene.focalDetail, 3),
    ].filter(Boolean);

    if (items.length >= 2) {
      return {
        type: 'process',
        items,
      };
    }
  }

  return null;
};

const EditorialGraphic = ({
  scene,
  theme,
}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();

  const graphic = editorialGraphicFor(scene);

  if (!graphic) return null;

  const enter = spring({
    fps,
    frame: Math.max(0, frame - Math.round(fps * 0.2)),
    config: {
      damping: 22,
      stiffness: 115,
      mass: 0.72,
    },
  });

  const base = {
    position: 'absolute',
    top: height > width ? '10%' : '8%',
    right: '4.5%',
    maxWidth: width > 1200 ? 460 : 350,
    padding: '14px 18px 16px',
    borderRadius: 15,
    background: 'rgba(5,10,18,.90)',
    border: `1px solid ${theme.accent}55`,
    boxShadow: '0 14px 34px rgba(0,0,0,.24)',
    color: '#FFFFFF',
    fontFamily: 'Manrope, Arial, sans-serif',
    opacity: enter,
    transform: `translateY(${(1 - enter) * -14}px)`,
    pointerEvents: 'none',
  };

  const labelStyle = {
    marginBottom: 7,
    color: theme.accent,
    fontSize: width > 1200 ? 13 : 11,
    fontWeight: 800,
    letterSpacing: 1.1,
    textTransform: 'uppercase',
  };

  if (graphic.type === 'stat') {
    return (
      <div style={base}>
        <div style={labelStyle}>Key figure</div>
        <div
          style={{
            color: theme.soft,
            fontSize: width > 1200 ? 42 : 32,
            fontWeight: 800,
            lineHeight: 1,
          }}
        >
          {graphic.value}
        </div>
        <div
          style={{
            marginTop: 8,
            fontSize: width > 1200 ? 18 : 15,
            fontWeight: 650,
          }}
        >
          {graphic.label}
        </div>
      </div>
    );
  }

  if (graphic.type === 'timeline') {
    return (
      <div style={base}>
        <div style={labelStyle}>Timeline</div>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <b>{graphic.from}</b>
          <div
            style={{
              flex: 1,
              height: 3,
              borderRadius: 99,
              background:
                `linear-gradient(90deg, ${theme.accent}, ${theme.accent2})`,
            }}
          />
          <b>{graphic.to}</b>
        </div>
      </div>
    );
  }

  if (graphic.type === 'comparison') {
    return (
      <div style={base}>
        <div style={labelStyle}>Comparison</div>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr auto 1fr',
            gap: 10,
            alignItems: 'center',
            fontSize: width > 1200 ? 17 : 14,
            fontWeight: 750,
          }}
        >
          <div>{graphic.left}</div>
          <div style={{color: theme.accent}}>VS</div>
          <div>{graphic.right}</div>
        </div>
      </div>
    );
  }

  if (graphic.type === 'process') {
    return (
      <div style={base}>
        <div style={labelStyle}>Process</div>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: width > 1200 ? 14 : 12,
            fontWeight: 700,
          }}
        >
          {graphic.items.map((item, index) => (
            <React.Fragment key={`${index}-${item}`}>
              {index > 0 ? (
                <span style={{color: theme.accent}}>→</span>
              ) : null}
              <span>{item}</span>
            </React.Fragment>
          ))}
        </div>
      </div>
    );
  }

  return null;
};

const shortFocus = (subject) => {
  const words = clean(subject).split(' ');
  return words.slice(0, 4).join(' ');
};

const FocusChip = ({
  scene,
  index,
  theme,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  if (index % 4 !== 1) return null;
  if (editorialGraphicFor(scene)) return null;

  const value = shortFocus(scene.subject);
  if (!value) return null;

  const enter = spring({
    fps,
    frame,
    config: {
      damping: 18,
      stiffness: 135,
    },
  });

  return (
    <div
      style={{
        position: 'absolute',
        top: '7%',
        left: '4.5%',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '8px 13px',
        borderRadius: 999,
        background: 'rgba(5,10,17,.88)',
        border:
          `1px solid ${theme.accent}66`,
        color: 'white',
        fontFamily: 'Manrope, Arial, sans-serif',
        fontSize: 18,
        fontWeight: 700,
        letterSpacing: 0.2,
        opacity: enter,
        transform:
          `translateY(${(1 - enter) * -14}px)`,
        boxShadow:
          `0 8px 30px ${theme.accent}22`,
      }}
    >
      <span
        style={{
          width: 9,
          height: 9,
          borderRadius: '50%',
          background: theme.accent,
          boxShadow:
            `0 0 14px ${theme.accent}`,
        }}
      />
      {value}
    </div>
  );
};

/* ---------------------------------------------------------
   RELEVANT EXPLANATION — TOP RIGHT ONLY
--------------------------------------------------------- */

const ExplanationCard = ({
  scene,
  index,
  theme,
}) => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();

  if (index % 5 !== 2) return null;
  if (editorialGraphicFor(scene)) return null;

  let text =
    clean(scene.focalDetail) ||
    clean(scene.action);

  if (!text || text.length > 85) {
    return null;
  }

  const enter = spring({
    fps,
    frame: Math.max(
      0,
      frame - Math.round(fps * 0.35)
    ),
    config: {
      damping: 18,
      stiffness: 120,
    },
  });

  return (
    <div
      style={{
        position: 'absolute',
        top: '8%',
        right: '4.5%',
        maxWidth:
          width > 1200 ? 430 : 340,
        opacity: enter,
        transform:
          `translateX(${(1 - enter) * 24}px)`,
        fontFamily: 'Manrope, Arial, sans-serif',
      }}
    >
      <div
        style={{
          width: 56,
          height: 4,
          borderRadius: 99,
          marginBottom: 8,
          background:
            `linear-gradient(90deg, ` +
            `${theme.accent}, ${theme.accent2})`,
          boxShadow:
            `0 0 16px ${theme.accent}66`,
        }}
      />

      <div
        style={{
          padding: '13px 17px',
          borderRadius: 15,
          color: 'white',
          fontSize:
            width > 1200 ? 20 : 17,
          lineHeight: 1.32,
          fontWeight: 590,
          background:
            'linear-gradient(145deg,' +
            'rgba(5,10,18,.88),' +
            'rgba(14,20,30,.88))',
          border:
            `1px solid ${theme.accent}45`,
          boxShadow:
            '0 12px 30px rgba(0,0,0,.24)',
        }}
      >
        {text}
      </div>
    </div>
  );
};

/* ---------------------------------------------------------
   CAPTIONS
   Phrase-based. No individual word boxes.
--------------------------------------------------------- */

const CAPTION_FONTS = {
  Manrope: 'Manrope, Arial, sans-serif',
  Inter: 'Inter, Arial, sans-serif',
  Montserrat: 'Montserrat, Arial, sans-serif',
  Poppins: 'Poppins, Arial, sans-serif',
  Roboto: 'Roboto, Arial, sans-serif',
  'Open Sans': '"Open Sans", Arial, sans-serif',
  Lato: 'Lato, Arial, sans-serif',
  Nunito: 'Nunito, Arial, sans-serif',
  Merriweather: 'Merriweather, Georgia, serif',
  'Source Sans 3': '"Source Sans 3", Arial, sans-serif',
};

const isLightColor = (hex) => {
  const value = String(hex || '#FFFFFF').replace('#', '');
  if (!/^[0-9a-fA-F]{6}$/.test(value)) return true;

  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);

  return ((0.2126 * r + 0.7152 * g + 0.0722 * b) / 255) >= 0.58;
};

const makeCaptionLines = (text) => {
  const words = clean(text).split(' ').filter(Boolean);

  if (words.length <= 6) {
    return [words.join(' ')];
  }

  const middle = Math.ceil(words.length / 2);

  return [
    words.slice(0, middle).join(' '),
    words.slice(middle).join(' '),
  ];
};

const PremiumCaptions = ({
  captions,
  captionStyle = {},
}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();

  const time = frame / fps;

  const cue = captions.find(
    (item) =>
      time >= Number(item.start) &&
      time < Number(item.end)
  );

  if (!cue) return null;

  const start = Number(cue.start);
  const end = Number(cue.end);
  const duration = Math.max(0.1, end - start);

  const age = frame - Math.round(start * fps);

  const enter = spring({
    fps,
    frame: Math.max(0, age),
    config: {
      damping: 24,
      stiffness: 125,
      mass: 0.72,
    },
  });

  const exitOpacity = interpolate(
    time,
    [
      Math.max(start, end - Math.min(0.18, duration * 0.3)),
      end,
    ],
    [1, 0],
    clamp
  );

  const fontColor =
    /^#[0-9a-fA-F]{6}$/.test(String(captionStyle.fontColor || ''))
      ? captionStyle.fontColor
      : '#FFFFFF';

  const lightText = isLightColor(fontColor);
  const lines = makeCaptionLines(cue.text);

  const longest = Math.max(
    ...lines.map((line) => line.length),
    1
  );

  const requestedSize = Number(captionStyle.fontSize || 48);

  const fitScale =
    longest > 58 ? 0.74 :
    longest > 48 ? 0.82 :
    longest > 40 ? 0.90 : 1;

  const fontSize = Math.max(
    24,
    Math.round(
      requestedSize *
      fitScale *
      (width <= 1080 ? 0.94 : 1)
    )
  );

  const position =
    ['top', 'middle', 'bottom'].includes(captionStyle.position)
      ? captionStyle.position
      : 'bottom';

  const background = captionStyle.background !== false;

  const outlineValue = Number(captionStyle.outline);
  const outline =
    [0, 1, 2, 4].includes(outlineValue)
      ? outlineValue
      : 2;

  const placement =
    position === 'top'
      ? {top: height > width ? '7%' : '5.5%'}
      : position === 'middle'
        ? {top: '50%'}
        : {bottom: height > width ? '7%' : '5.3%'};

  const glassBackground =
    lightText
      ? 'rgba(7,11,18,.72)'
      : 'rgba(248,250,252,.78)';

  const glassBorder =
    lightText
      ? 'rgba(255,255,255,.12)'
      : 'rgba(7,11,18,.12)';

  const outlineColor =
    lightText
      ? 'rgba(0,0,0,.82)'
      : 'rgba(255,255,255,.90)';

  return (
    <div
      style={{
        position: 'absolute',
        left: '50%',
        ...placement,
        width: width > 1200 ? '82%' : '90%',
        display: 'flex',
        justifyContent: 'center',
        opacity: Math.min(enter, exitOpacity),
        transform:
          `translateX(-50%) ` +
          `${position === 'middle' ? 'translateY(-50%) ' : ''}` +
          `translateY(${(1 - enter) * 10}px)`,
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          maxWidth: '100%',
          padding: background
            ? (height > width ? '10px 18px 12px' : '9px 17px 11px')
            : 0,
          borderRadius: background ? 14 : 0,
          background: background ? glassBackground : 'transparent',
          border: background ? `1px solid ${glassBorder}` : 'none',
          boxShadow: background
            ? '0 10px 30px rgba(0,0,0,.18)'
            : 'none',
          color: fontColor,
          fontFamily:
            CAPTION_FONTS[captionStyle.fontFamily] ||
            CAPTION_FONTS.Manrope,
          fontSize,
          fontWeight: 700,
          lineHeight: 1.18,
          letterSpacing: '-0.012em',
          textAlign: 'center',
          WebkitFontSmoothing: 'antialiased',
          WebkitTextStroke:
            outline > 0
              ? `${outline}px ${outlineColor}`
              : '0 transparent',
          paintOrder: 'stroke fill',
          textShadow:
            outline > 0
              ? '0 3px 12px rgba(0,0,0,.25)'
              : (
                  lightText
                    ? '0 3px 12px rgba(0,0,0,.82)'
                    : '0 2px 10px rgba(255,255,255,.35)'
                ),
        }}
      >
        {lines.map((line, index) => (
          <React.Fragment key={`${index}-${line}`}>
            {index > 0 ? <br /> : null}
            {line}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
};

/* ---------------------------------------------------------
   CTA — TRUE CTA ONLY, LAST FEW SECONDS
--------------------------------------------------------- */

const hasCTA = (text) => {
  const t = clean(text).toLowerCase();

  return (
    t.includes('subscribe') ||
    (
      t.includes('comment') &&
      t.includes('like')
    )
  );
};

const CTA = ({
  durationInFrames,
  theme,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  const start =
    Math.max(
      0,
      durationInFrames -
      Math.round(fps * 4)
    );

  if (frame < start) return null;

  const enter = spring({
    fps,
    frame: frame - start,
    config: {
      damping: 16,
      stiffness: 110,
    },
  });

  return (
    <div
      style={{
        position: 'absolute',
        left: '50%',
        top: '12%',
        transform:
          `translateX(-50%) ` +
          `scale(${0.94 + enter * 0.06})`,
        opacity: enter,
        padding: '12px 23px',
        borderRadius: 999,
        color: 'white',
        fontFamily:
          'Inter, Arial, sans-serif',
        fontWeight: 760,
        fontSize: 24,
        background:
          `linear-gradient(100deg, ` +
          `${theme.accent}DD, ` +
          `${theme.accent2}DD)`,
        boxShadow:
          `0 15px 45px ${theme.accent}44`,
      }}
    >
      Like · Subscribe · Comment
    </div>
  );
};

/* ---------------------------------------------------------
   SCENE
--------------------------------------------------------- */

const PremiumScene = ({
  scene,
  index,
  durationInFrames,
  isLast,
  theme,
}) => (
  <AbsoluteFill>
    <SceneMedia
      scene={scene}
      index={index}
      durationInFrames={durationInFrames}
      theme={theme}
    />

    <EditorialGraphic
      scene={scene}
      theme={theme}
    />

    <FocusChip
      scene={scene}
      index={index}
      theme={theme}
    />

    <ExplanationCard
      scene={scene}
      index={index}
      theme={theme}
    />

    {isLast &&
    hasCTA(scene.narration) ? (
      <CTA
        durationInFrames={durationInFrames}
        theme={theme}
      />
    ) : null}
  </AbsoluteFill>
);

/* ---------------------------------------------------------
   MAIN
--------------------------------------------------------- */

export const AttaVideo = ({
  scenes = [],
  captions = [],
  captionStyle = {},
  audio = '',
}) => {
  const {fps} = useVideoConfig();

  const theme = getTheme(scenes);

  const durations = scenes.map(
    (scene) =>
      Math.max(
        1,
        Math.round(
          Number(scene.duration || 0.05) * fps
        )
      )
  );

  return (
    <AbsoluteFill
      style={{
        backgroundColor: theme.dark,
      }}
    >
      <TransitionSeries>
        {scenes.map((scene, index) => {
          const isLast =
            index === scenes.length - 1;

          const transition =
            isLast
              ? 0
              : transitionFrames(scene, index);

          return (
            <React.Fragment
              key={scene.sceneNumber}
            >
              <TransitionSeries.Sequence
                durationInFrames={
                  durations[index] + transition
                }
                premountFor={fps}
              >
                <PremiumScene
                  scene={scene}
                  index={index}
                  durationInFrames={
                    durations[index]
                  }
                  isLast={isLast}
                  theme={theme}
                />
              </TransitionSeries.Sequence>

              {!isLast && transition > 0 ? (
                <TransitionSeries.Transition
                  presentation={
                    transitionPresentation(scene, index)
                  }
                  timing={
                    linearTiming({
                      durationInFrames:
                        transition,
                    })
                  }
                />
              ) : null}
            </React.Fragment>
          );
        })}
      </TransitionSeries>

      {audio ? (
        <Audio src={staticFile(audio)} />
      ) : null}

      <PremiumCaptions
        captions={captions}
        captionStyle={captionStyle}
      />
    </AbsoluteFill>
  );
};
