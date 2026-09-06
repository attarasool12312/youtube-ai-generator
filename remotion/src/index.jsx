import React from 'react';
import {
  Composition,
  registerRoot,
} from 'remotion';
import {AttaVideo} from './video';

const Root = () => {
  return (
    <Composition
      id="ATTA"
      component={AttaVideo}
      durationInFrames={30}
      fps={30}
      width={1920}
      height={1080}
      defaultProps={{
        width: 1920,
        height: 1080,
        fps: 30,
        durationInFrames: 30,
        scenes: [],
        captions: [],
        audio: '',
        orientation: 'landscape',
      }}
      calculateMetadata={({props}) => ({
        durationInFrames: Math.max(
          1,
          Number(props.durationInFrames || 30)
        ),
        fps: Number(props.fps || 30),
        width: Number(props.width || 1920),
        height: Number(props.height || 1080),
        props,
      })}
    />
  );
};

registerRoot(Root);
