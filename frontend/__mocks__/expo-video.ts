import React from 'react';
import { View } from 'react-native';

export const VideoView = (props: any) =>
  React.createElement(View, { testID: 'video-preview', ...props });

export const useVideoPlayer = (_source: any, setup?: (player: any) => void) => {
  const player = {
    loop: false,
    play: jest.fn(),
    pause: jest.fn(),
    release: jest.fn(),
  };
  if (setup) setup(player);
  return player;
};
