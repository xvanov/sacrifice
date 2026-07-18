import { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  Platform,
  Pressable,
  Text,
  View,
} from 'react-native';
import { launchImageLibraryAsync, requestMediaLibraryPermissionsAsync } from 'expo-image-picker';
import CameraCapture from './CameraCapture';
import { api } from '../services/api';

type UploadState =
  | { phase: 'idle' }
  | { phase: 'capturing' }
  | { phase: 'uploading' }
  | { phase: 'done'; uploadId: string }
  | { phase: 'error'; message: string };

interface Props {
  goalId?: string;
  onUploaded?: (uploadResult: { upload_id: string; sha256: string; size_bytes: number; duration_seconds: number; mime_type: string }) => void;
  onCancel?: () => void;
}

export default function MediaUploader({ goalId, onUploaded, onCancel }: Props) {
  const [uploadState, setUploadState] = useState<UploadState>({ phase: 'idle' });

  const handleCaptured = useCallback(
    async (asset: { uri: string }) => {
      setUploadState({ phase: 'uploading' });
      const result = await api.uploadVideo(
        { uri: asset.uri, fileName: 'recording.mp4', type: 'video/mp4' },
        // Duration is recorded server-side from the file; pass a best-effort 0
        // since we can't measure it accurately from CameraCapture's callback.
        0,
        goalId,
      );
      if (result.error) {
        setUploadState({ phase: 'error', message: result.error });
      } else if (result.data) {
        setUploadState({ phase: 'done', uploadId: result.data.upload_id });
        onUploaded?.(result.data);
      }
    },
    [goalId, onUploaded],
  );

  const handlePickFromLibrary = useCallback(async () => {
    const permission = await requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      setUploadState({ phase: 'error', message: 'Media library permission denied. Please enable it in Settings to upload a proof video.' });
      return;
    }

    const result = await launchImageLibraryAsync({
      mediaTypes: 'videos',
      allowsEditing: false,
      quality: 1,
    });

    if (result.canceled || !result.assets || result.assets.length === 0) {
      return;
    }

    const asset = result.assets[0];
    setUploadState({ phase: 'uploading' });
    const uploadResult = await api.uploadVideo(
      {
        uri: asset.uri,
        fileName: asset.fileName || 'library_video.mp4',
        type: asset.mimeType || 'video/mp4',
      },
      asset.duration ? Math.round(asset.duration) : 0,
      goalId,
    );
    if (uploadResult.error) {
      setUploadState({ phase: 'error', message: uploadResult.error });
    } else if (uploadResult.data) {
      setUploadState({ phase: 'done', uploadId: uploadResult.data.upload_id });
      onUploaded?.(uploadResult.data);
    }
  }, [goalId, onUploaded]);

  const handleOpenCamera = useCallback(() => {
    setUploadState({ phase: 'capturing' });
  }, []);

  const handleCameraCancel = useCallback(() => {
    setUploadState({ phase: 'idle' });
    onCancel?.();
  }, [onCancel]);

  // -- Web path: file input ---------------------------------------------------
  const isWeb = Platform.OS === 'web';

  if (isWeb) {
    return (
      <View className="mb-4">
        {uploadState.phase === 'uploading' && (
          <View className="items-center py-4">
            <ActivityIndicator size="small" color="#8A2A1C" />
            <Text className="mt-2 font-sans text-sm text-codex-muted">Uploading...</Text>
          </View>
        )}
        {uploadState.phase === 'done' && (
          <View className="rounded-sm border border-codex-border bg-codex-surface p-3">
            <Text className="font-sans text-sm text-codex-text">Video uploaded successfully</Text>
            <Text className="mt-1 font-mono text-xs text-codex-muted" numberOfLines={1}>
              {uploadState.uploadId}
            </Text>
          </View>
        )}
        {uploadState.phase === 'error' && (
          <View className="rounded-sm border border-codex-accent bg-codex-surface p-3">
            <Text className="font-sans text-sm text-codex-accent">{uploadState.message}</Text>
          </View>
        )}
        {uploadState.phase !== 'uploading' && (
          <Pressable
            className="rounded-sm border border-codex-border bg-codex-surface px-4 py-3 active:bg-codex-bg"
            onPress={() => {
              // Guard: only execute on web where document is available
              if (typeof document === 'undefined') return;
              const input = document.createElement('input');
              input.type = 'file';
              input.accept = 'video/mp4,video/quicktime';
              input.onchange = async (e: Event) => {
                const file = (e.target as HTMLInputElement).files?.[0];
                if (!file) return;
                setUploadState({ phase: 'uploading' });
                const result = await api.uploadVideo(
                  { uri: URL.createObjectURL(file), fileName: file.name, type: file.type },
                  file.type.startsWith('video/') ? 0 : 0,
                  goalId,
                );
                if (result.error) {
                  setUploadState({ phase: 'error', message: result.error });
                } else if (result.data) {
                  setUploadState({ phase: 'done', uploadId: result.data.upload_id });
                  onUploaded?.(result.data);
                }
              };
              input.click();
            }}
          >
            <Text className="text-center font-sans-medium text-sm text-codex-text">
              Choose video file
            </Text>
          </Pressable>
        )}
      </View>
    );
  }

  // -- Native path: camera capture or library pick ---------------------------
  if (uploadState.phase === 'capturing') {
    return (
      <CameraCapture
        maxDurationSeconds={300}
        onCaptured={handleCaptured}
        onCancel={handleCameraCancel}
      />
    );
  }

  if (uploadState.phase === 'idle') {
    return (
      <View className="mb-4 flex-row gap-x-3">
        <Pressable
          testID="take-photo-button"
          className="flex-1 rounded-sm bg-codex-accent px-4 py-3 active:bg-codex-accent-light"
          onPress={handleOpenCamera}
        >
          <Text className="text-center font-sans-medium text-sm text-codex-surface">Take Photo</Text>
        </Pressable>
        <Pressable
          testID="pick-from-library-button"
          className="flex-1 rounded-sm border border-codex-border bg-codex-surface px-4 py-3 active:bg-codex-bg"
          onPress={handlePickFromLibrary}
        >
          <Text className="text-center font-sans-medium text-sm text-codex-text">Choose from Library</Text>
        </Pressable>
      </View>
    );
  }

  if (uploadState.phase === 'uploading') {
    return (
      <View className="mb-4 items-center py-4">
        <ActivityIndicator size="small" color="#8A2A1C" />
        <Text className="mt-2 font-sans text-sm text-codex-muted">Uploading video...</Text>
      </View>
    );
  }

  if (uploadState.phase === 'done') {
    return (
      <View testID="upload-done" className="mb-4 rounded-sm border border-codex-border bg-codex-surface p-3">
        <Text className="font-sans text-sm text-codex-text">Video uploaded successfully</Text>
        <Text className="mt-1 font-mono text-xs text-codex-muted" numberOfLines={1}>
          {uploadState.uploadId}
        </Text>
      </View>
    );
  }

  if (uploadState.phase === 'error') {
    return (
      <View testID="upload-error" className="mb-4 rounded-sm border border-codex-accent bg-codex-surface p-3">
        <Text className="font-sans text-sm text-codex-accent">{uploadState.message}</Text>
        <Pressable
          className="mt-2 self-start"
          onPress={() => setUploadState({ phase: 'idle' })}
        >
          <Text className="font-sans-medium text-sm text-codex-accent">Try again</Text>
        </Pressable>
      </View>
    );
  }

  return null;
}