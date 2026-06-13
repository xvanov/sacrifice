import React from 'react';
import { View } from 'react-native';

export const Video = React.forwardRef((props: any, ref: any) => {
  React.useImperativeHandle(ref, () => ({
    presentFullscreenPlayer: jest.fn(),
    dismissFullscreenPlayer: jest.fn(),
    playAsync: jest.fn().mockResolvedValue(undefined),
    pauseAsync: jest.fn().mockResolvedValue(undefined),
    stopAsync: jest.fn().mockResolvedValue(undefined),
    unloadAsync: jest.fn().mockResolvedValue(undefined),
    setStatusAsync: jest.fn().mockResolvedValue(undefined),
    getStatusAsync: jest.fn().mockResolvedValue({ isLoaded: true }),
  }));
  return React.createElement(View, { testID: 'video-preview', ...props });
});

export const Audio = {
  setAudioModeAsync: jest.fn().mockResolvedValue(undefined),
};

export const ResizeMode = {
  COVER: 'cover',
  CONTAIN: 'contain',
  STRETCH: 'stretch',
};