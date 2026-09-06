import React from 'react';

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

const PALETTES = [
  {
    accent: '#37C9FF',
    accent2: '#7868FF',
    soft: '#C9F3FF',
    dark: '#07111D',
  },
  {
    accent: '#F4B942',
    accent2: '#F97316',
    soft: '#FFE7A8',
    dark: '#151006',
  },
  {
    accent: '#36D399',
    accent2: '#22A7F0',
    soft: '#C5FBE8',
    dark: '#061510',
  },
  {
    accent: '#E879F9',
    accent2: '#8B5CF6',
    soft: '#F4D5FF',
    dark: '#12091A',
  },
  {
    accent: '#FF6B7A',
    accent2: '#FF9F43',
    soft: '#FFD6DA',
    dark: '#18080B',
  },
  {
    accent: '#69A9FF',
    accent2: '#38E0D0',
    soft: '#D2E6FF',
    dark: '#07101A',
  },
];

const getTheme = (scenes) => {
  const signature = scenes
    .slice(0, 12)
    .map((s) => `${s.subject} ${s.environment}`)
    .join('|');

  return PALETTES[
    hash(signature) % PALETTES.length
  ];
};

/* ---------------------------------------------------------
   TRANSITIONS
--------------------------------------------------------- */

const transitionFrames = (index) =>
  [20, 24, 27, 18, 23, 29, 21, 25][
    index % 8
  ];

const transitionPresentation = (index) => {
  const variants = [
    linearBlur,
    crossZoom,
    pushCut,
    dreamyZoom,
    linearBlur,
    crossZoom,
  ];

  try {
    return variants[index % variants.length]();
  } catch {
    return linearBlur();
  }
};

/* ---------------------------------------------------------
   IMAGE MOTION
--------------------------------------------------------- */

const getMotion = (scene, index) => {
  const text = clean(
    `${scene.shotType} ${scene.cameraAngle}`
  ).toLowerCase();

  if (text.includes('macro')) return 'macro';
  if (text.includes('overhead')) return 'overhead';
  if (text.includes('aerial')) return 'aerial';
  if (text.includes('tracking')) return 'tracking';
  if (text.includes('low')) return 'rise';

  return [
    'push',
    'pull',
    'left',
    'right',
    'rise',
    'float',
    'hold',
  ][hash(`${scene.subject}-${index}`) % 7];
};

const motionValues = (preset, p) => {
  switch (preset) {
    case 'pull':
      return {
        scale: 1.14 - p * 0.10,
        x: 0,
        y: 0,
        rotate: 0,
      };

    case 'left':
      return {
        scale: 1.09,
        x: 3.8 - p * 7.6,
        y: 0,
        rotate: -0.25,
      };

    case 'right':
      return {
        scale: 1.09,
        x: -3.8 + p * 7.6,
        y: 0,
        rotate: 0.25,
      };

    case 'rise':
      return {
        scale: 1.08 + p * 0.025,
        x: 0,
        y: 3.2 - p * 6.4,
        rotate: -0.15,
      };

    case 'macro':
      return {
        scale: 1.05 + p * 0.075,
        x: -1 + p * 2,
        y: 0.7 - p * 1.4,
        rotate: 0,
      };

    case 'overhead':
    case 'aerial':
      return {
        scale: 1.08,
        x: -2.5 + p * 5,
        y: 1.4 - p * 2.8,
        rotate: 0.18,
      };

    case 'tracking':
      return {
        scale: 1.055,
        x: -4 + p * 8,
        y: 0,
        rotate: 0,
      };

    case 'hold':
      return {
        scale: 1.045,
        x: 0,
        y: 0,
        rotate: 0,
      };

    case 'float':
      return {
        scale: 1.07 + p * 0.025,
        x: Math.sin(p * Math.PI) * 1.5,
        y: -1.2 + p * 2.4,
        rotate: 0.15,
      };

    default:
      return {
        scale: 1.035 + p * 0.09,
        x: 0,
        y: 0,
        rotate: 0,
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
        padding: '9px 14px',
        borderRadius: 999,
        background: 'rgba(5,10,17,.66)',
        border:
          `1px solid ${theme.accent}88`,
        backdropFilter: 'blur(12px)',
        color: 'white',
        fontFamily: 'Inter, Arial, sans-serif',
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
        fontFamily: 'Inter, Arial, sans-serif',
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
            'rgba(5,10,18,.78),' +
            'rgba(20,28,40,.60))',
          border:
            `1px solid ${theme.accent}4F`,
          boxShadow:
            '0 15px 42px rgba(0,0,0,.28)',
          backdropFilter: 'blur(12px)',
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

const splitPhrases = (words) => {
  if (words.length <= 4) return [words];

  const size =
    words.length >= 9 ? 3 : 2;

  const groups = [];
  for (let i = 0; i < words.length; i += size) {
    groups.push(words.slice(i, i + size));
  }
  return groups;
};

const PremiumCaptions = ({
  captions,
  theme,
}) => {
  const frame = useCurrentFrame();
  const {
    fps,
    width,
    height,
  } = useVideoConfig();

  const time = frame / fps;

  const cue = captions.find(
    (item) =>
      time >= Number(item.start) &&
      time < Number(item.end)
  );

  if (!cue) return null;

  const words = clean(cue.text)
    .split(' ')
    .filter(Boolean);

  const groups = splitPhrases(words);

  const start = Number(cue.start);
  const end = Number(cue.end);
  const duration = Math.max(0.1, end - start);

  const progress = Math.max(
    0,
    Math.min(0.999, (time - start) / duration)
  );

  const active = Math.min(
    groups.length - 1,
    Math.floor(progress * groups.length)
  );

  const age =
    frame - Math.round(start * fps);

  const enter = spring({
    fps,
    frame: Math.max(0, age),
    config: {
      damping: 20,
      stiffness: 155,
      mass: 0.65,
    },
  });

  return (
    <div
      style={{
        position: 'absolute',
        left: '50%',
        bottom:
          height > width ? '7%' : '5.3%',
        transform:
          `translateX(-50%) ` +
          `translateY(${(1 - enter) * 12}px)`,
        width:
          width > 1200 ? '82%' : '90%',
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        flexWrap: 'wrap',
        columnGap: 10,
        rowGap: 7,
        opacity: enter,
        textAlign: 'center',
        fontFamily:
          'Inter, Arial, sans-serif',
        fontSize:
          width > 1200 ? 38 : 31,
        fontWeight: 760,
        lineHeight: 1.22,
        textShadow:
          '0 3px 14px rgba(0,0,0,.92)',
      }}
    >
      {groups.map((group, index) => {
        const highlighted =
          index === active;

        return (
          <span
            key={index}
            style={{
              display: 'inline-block',
              padding:
                highlighted
                  ? '4px 10px 5px'
                  : '4px 3px 5px',
              borderRadius: 9,
              color:
                highlighted
                  ? '#061018'
                  : index < active
                    ? 'rgba(255,255,255,.72)'
                    : '#FFFFFF',
              background:
                highlighted
                  ? `linear-gradient(100deg, ` +
                    `${theme.accent}, ${theme.accent2})`
                  : 'transparent',
              boxShadow:
                highlighted
                  ? `0 6px 24px ${theme.accent}44`
                  : 'none',
              transform:
                highlighted
                  ? 'scale(1.025)'
                  : 'scale(1)',
            }}
          >
            {group.join(' ')}
          </span>
        );
      })}
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
              : transitionFrames(index);

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

              {!isLast ? (
                <TransitionSeries.Transition
                  presentation={
                    transitionPresentation(index)
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
        theme={theme}
      />
    </AbsoluteFill>
  );
};
