import { useState, useEffect, useRef, useCallback } from 'react';
import { Text, View, Pressable } from 'react-native';
import { CameraView, useCameraPermissions, useMicrophonePermissions } from 'expo-camera';
import { openSettings } from 'expo-linking';

interface CameraCaptureProps {
  onCaptured: (asset: { uri: string }) => void;
  onCancel: () => void;
  maxDurationSeconds?: number;
}

type CaptureState = 'loading' | 'denied' | 'ready' | 'recording' | 'captured';

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export default function CameraCapture({
  onCaptured,
  onCancel,
  maxDurationSeconds,
}: CameraCaptureProps) {
  const [cameraPermission] = useCameraPermissions();
  const [microphonePermission] = useMicrophonePermissions();
  const [captureState, setCaptureState] = useState<CaptureState>('loading');
  const [recordedAsset, setRecordedAsset] = useState<{ uri: string } | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const cameraRef = useRef<CameraView>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const maxDurationRef = useRef<number | undefined>(maxDurationSeconds);
  maxDurationRef.current = maxDurationSeconds;

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => clearTimer, [clearTimer]);

  useEffect(() => {
    if (!cameraPermission || !microphonePermission) return;

    const cameraGranted = cameraPermission.granted;
    const micGranted = microphonePermission.granted;
    const cameraDeniedNoAsk = !cameraPermission.granted && !cameraPermission.canAskAgain;
    const micDeniedNoAsk = !microphonePermission.granted && !microphonePermission.canAskAgain;

    if (cameraGranted && micGranted) {
      setCaptureState('ready');
    } else if (cameraDeniedNoAsk || micDeniedNoAsk) {
      setCaptureState('denied');
    }
  }, [cameraPermission, microphonePermission]);

  const handleStartRecording = useCallback(async () => {
    const cam = cameraRef.current;
    if (!cam) return;

    setCaptureState('recording');
    setElapsedSeconds(0);

    timerRef.current = setInterval(() => {
      setElapsedSeconds((prev) => {
        const next = prev + 1;
        if (maxDurationRef.current !== undefined && next >= maxDurationRef.current) {
          return next;
        }
        return next;
      });
    }, 1000);

    try {
      const video = await cam.recordAsync(
        maxDurationRef.current !== undefined ? { maxDuration: maxDurationRef.current } : undefined,
      );
      if (video) {
        setRecordedAsset(video);
        setCaptureState('captured');
      }
    } catch {
      setCaptureState('ready');
    } finally {
      clearTimer();
    }
  }, [clearTimer]);

  const handleStopRecording = useCallback(() => {
    cameraRef.current?.stopRecording();
  }, []);

  const handleUseVideo = useCallback(() => {
    if (recordedAsset) {
      onCaptured(recordedAsset);
    }
  }, [recordedAsset, onCaptured]);

  const handleRetake = useCallback(() => {
    setRecordedAsset(null);
    setCaptureState('ready');
  }, []);

  if (captureState === 'denied') {
    return (
      <View className="flex-1 items-center justify-center bg-codex-bg p-6">
        <Text className="mb-4 text-center font-sans text-base text-codex-text">
          Camera access is required to submit this proof
        </Text>
        <Pressable
          className="mb-3 rounded-sm bg-codex-accent px-6 py-3"
          onPress={openSettings}
        >
          <Text className="font-sans-medium text-base text-codex-surface">Open settings</Text>
        </Pressable>
        <Pressable onPress={onCancel}>
          <Text className="font-sans text-sm text-codex-accent">Cancel</Text>
        </Pressable>
      </View>
    );
  }

  if (captureState === 'loading') {
    return (
      <View className="flex-1 items-center justify-center bg-codex-bg">
        <Text className="font-sans text-sm text-codex-muted">Requesting permissions…</Text>
      </View>
    );
  }

  if (captureState === 'captured' && recordedAsset) {
    return (
      <View className="flex-1 items-center justify-center bg-codex-bg p-6">
        <Text className="mb-6 font-sans text-base text-codex-text">Recording complete</Text>
        <View className="mb-6 flex-row gap-4">
          <Pressable
            className="rounded-sm border border-codex-border bg-codex-surface px-6 py-3"
            onPress={handleRetake}
          >
            <Text className="font-sans-medium text-base text-codex-text">Retake</Text>
          </Pressable>
          <Pressable
            className="rounded-sm bg-codex-accent px-6 py-3"
            onPress={handleUseVideo}
          >
            <Text className="font-sans-medium text-base text-codex-surface">Use this video</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  const isRecording = captureState === 'recording';

  return (
    <View className="flex-1 bg-codex-dark">
      <CameraView ref={cameraRef} mode="video" className="flex-1" />
      <View className="absolute bottom-0 left-0 right-0 items-center pb-12 pt-8">
        {isRecording && (
          <Text className="mb-4 font-mono text-lg text-white">
            {formatElapsed(elapsedSeconds)}
          </Text>
        )}
        <Pressable
          className={`rounded-full px-10 py-4 ${isRecording ? 'bg-red-600' : 'bg-codex-accent'}`}
          onPress={isRecording ? handleStopRecording : handleStartRecording}
        >
          <Text className="font-sans-medium text-base text-white">
            {isRecording ? 'Stop recording' : 'Start recording'}
          </Text>
        </Pressable>
      </View>
    </View>
  );
}