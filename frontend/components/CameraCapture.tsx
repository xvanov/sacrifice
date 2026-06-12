import { useEffect, useRef, useState, useCallback } from 'react';
import { Linking, Platform, Pressable, Text, View } from 'react-native';
import { CameraView, useCameraPermissions, useMicrophonePermissions } from 'expo-camera';
import { VideoView, useVideoPlayer } from 'expo-video';

type CaptureStatus = 'loading' | 'denied' | 'ready' | 'recording' | 'stopping' | 'preview';

interface Props {
  maxDurationSeconds?: number;
  onCaptured?: (asset: { uri: string }) => void;
  onCancel?: () => void;
}

export default function CameraCapture({ maxDurationSeconds, onCaptured, onCancel }: Props) {
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();
  const [microphonePermission, requestMicrophonePermission] = useMicrophonePermissions();
  const [status, setStatus] = useState<CaptureStatus>('loading');
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [recordedAsset, setRecordedAsset] = useState<{ uri: string } | null>(null);
  const cameraRef = useRef<CameraView | null>(null);
  const startTimeRef = useRef<number>(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const recordedAssetRef = useRef<{ uri: string } | null>(null);
  // expo-video player for the post-capture preview (Expo 54 replaces the
  // deprecated expo-av <Video>). Source follows the captured asset.
  const previewPlayer = useVideoPlayer(recordedAsset ? recordedAsset.uri : null, (player) => {
    player.loop = true;
    player.play();
  });

  useEffect(() => {
    if (!cameraPermission || !microphonePermission) {
      if (!cameraPermission) {
        requestCameraPermission();
      }
      if (!microphonePermission) {
        requestMicrophonePermission();
      }
      return;
    }

    if (cameraPermission.granted && microphonePermission.granted) {
      setStatus('ready');
      return;
    }

    setStatus('denied');
  }, [
    cameraPermission,
    microphonePermission,
    requestCameraPermission,
    requestMicrophonePermission,
  ]);

  // Elapsed-time ticker while recording
  useEffect(() => {
    if (status === 'recording') {
      startTimeRef.current = Date.now();
      timerRef.current = setInterval(() => {
        setElapsedSeconds((Date.now() - startTimeRef.current) / 1000);
      }, 200);
    } else {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }
    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [status]);

  // Auto-stop when maxDurationSeconds is reached
  useEffect(() => {
    if (status !== 'recording' || !maxDurationSeconds) return;
    if (elapsedSeconds >= maxDurationSeconds) {
      stopRecording();
    }
  }, [status, elapsedSeconds, maxDurationSeconds]);

  const startRecording = useCallback(async () => {
    if (!cameraRef.current) return;
    // Reset any prior capture so a recording completed after Retake is
    // not ignored by the stale-ref guard below.
    recordedAssetRef.current = null;
    setRecordedAsset(null);
    setStatus('recording');
    setElapsedSeconds(0);
    try {
      const asset = await cameraRef.current.recordAsync({ maxDuration: maxDurationSeconds });
      if (asset && !recordedAssetRef.current) {
        recordedAssetRef.current = asset;
        setRecordedAsset(asset);
        setStatus('preview');
        return;
      }
      if (!recordedAssetRef.current) {
        setStatus('ready');
      }
    } catch {
      if (!recordedAssetRef.current) {
        setStatus('ready');
      }
    }
  }, [maxDurationSeconds]);

  const stopRecording = useCallback(async () => {
    if (!cameraRef.current) return;
    setStatus('stopping');
    try {
      await cameraRef.current.stopRecording();
    } catch {
      if (!recordedAssetRef.current) {
        setStatus('ready');
      }
    }
  }, []);

  const handleRetake = useCallback(() => {
    recordedAssetRef.current = null;
    setRecordedAsset(null);
    setElapsedSeconds(0);
    setStatus('ready');
  }, []);

  const handleUseVideo = useCallback(() => {
    if (recordedAsset && onCaptured) {
      onCaptured(recordedAsset);
    }
  }, [recordedAsset, onCaptured]);

  const handleOpenSettings = useCallback(() => {
    if (Platform.OS === 'ios') {
      Linking.openURL('app-settings:');
    } else {
      Linking.openSettings();
    }
  }, []);

  const formatElapsed = (s: number): string => {
    const mins = Math.floor(s / 60);
    const secs = Math.floor(s % 60);
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  };

  // -- Denied state ----------------------------------------------------------
  if (status === 'denied') {
    return (
      <View className="flex-1 items-center justify-center bg-codex-bg px-6">
        <Text className="mb-4 text-center font-sans-medium text-lg text-codex-text">
          Camera access is required to submit this proof
        </Text>
        <Pressable
          className="mb-3 rounded-sm bg-codex-accent px-6 py-3 active:bg-codex-accent-light"
          onPress={handleOpenSettings}
        >
          <Text className="font-sans-medium text-base text-codex-surface">Open settings</Text>
        </Pressable>
        <Pressable className="py-2" onPress={onCancel}>
          <Text className="font-sans-medium text-base text-codex-accent">Cancel</Text>
        </Pressable>
      </View>
    );
  }

  // -- Loading state ---------------------------------------------------------
  if (status === 'loading') {
    return (
      <View className="flex-1 items-center justify-center bg-codex-bg">
        <Text className="font-sans text-sm text-codex-muted">Requesting camera permission…</Text>
      </View>
    );
  }

  // -- Ready / Recording / Stopping / Preview states --------------------------
  return (
    <View className="flex-1 bg-black">
      {/* Camera or video preview */}
      <View className="flex-1">
        {status === 'preview' && recordedAsset ? (
          <VideoView
            player={previewPlayer}
            contentFit="cover"
            nativeControls={false}
            className="flex-1"
            testID="video-preview"
          />
        ) : (
          <CameraView
            ref={cameraRef}
            mode="video"
            className="flex-1"
          />
        )}
      </View>

      {/* Controls overlay */}
      <View className="absolute bottom-0 left-0 right-0 items-center pb-10">
        {status === 'ready' && (
          <Pressable
            className="rounded-full bg-codex-accent px-8 py-4 active:bg-codex-accent-light"
            onPress={startRecording}
          >
            <Text className="font-sans-medium text-base text-codex-surface">Start recording</Text>
          </Pressable>
        )}

        {status === 'recording' && (
          <View className="items-center">
            <View className="mb-4 flex-row items-center gap-x-2">
              <View className="h-3 w-3 rounded-full bg-red-500" />
              <Text className="font-mono text-lg text-white">{formatElapsed(elapsedSeconds)}</Text>
            </View>
            <Pressable
              className="rounded-full border-2 border-white bg-red-500 px-8 py-4 active:bg-red-600"
              onPress={stopRecording}
            >
              <Text className="font-sans-medium text-base text-white">Stop recording</Text>
            </Pressable>
          </View>
        )}

        {status === 'stopping' && (
          <Text className="font-sans text-sm text-white">Finishing recording…</Text>
        )}

        {status === 'preview' && recordedAsset && (
          <View className="w-full flex-row justify-center gap-x-6 px-6">
            <Pressable
              className="flex-1 rounded-sm border border-codex-border bg-codex-surface px-6 py-3.5 active:bg-codex-bg"
              onPress={handleRetake}
            >
              <Text className="text-center font-sans-medium text-base text-codex-text">Retake</Text>
            </Pressable>
            <Pressable
              className="flex-1 rounded-sm bg-codex-accent px-6 py-3.5 active:bg-codex-accent-light"
              onPress={handleUseVideo}
            >
              <Text className="text-center font-sans-medium text-base text-codex-surface">Use this video</Text>
            </Pressable>
          </View>
        )}
      </View>
    </View>
  );
}