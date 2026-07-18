import { render, fireEvent, act } from '@testing-library/react-native';
import { Platform } from 'react-native';
import MediaUploader from '../../components/MediaUploader';
import { mockCamera } from '../../__mocks__/expo-camera';
import { mockImagePicker } from '../../__mocks__/expo-image-picker';

// We need to spy on api.uploadVideo and api.submitMediaProof
import { api } from '../../services/api';

jest.mock('../../services/api', () => ({
  api: {
    uploadVideo: jest.fn(),
    submitMediaProof: jest.fn(),
  },
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function grantedPermissions() {
  return { granted: true, canAskAgain: true, expires: 'never' };
}

function deniedPermissions() {
  return { granted: false, canAskAgain: true, expires: 'never' };
}

// ---------------------------------------------------------------------------
describe('MediaUploader', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.clearAllMocks();
    mockCamera.getPermissions.mockReturnValue(grantedPermissions());
    mockCamera.getMicrophonePermissions.mockReturnValue(grantedPermissions());
    mockCamera.requestPermissions.mockResolvedValue(grantedPermissions());
    mockCamera.requestMicrophonePermissions.mockResolvedValue(grantedPermissions());
    mockImagePicker.requestMediaLibraryPermissionsAsync.mockResolvedValue(grantedPermissions());
    mockImagePicker.getMediaLibraryPermissionsAsync.mockResolvedValue(grantedPermissions());
    mockImagePicker.launchImageLibraryAsync.mockResolvedValue({
      canceled: false,
      assets: [{ uri: 'file:///tmp/library.mp4', fileName: 'library.mp4', mimeType: 'video/mp4', duration: 5 }],
    });
    mockImagePicker.launchCameraAsync.mockResolvedValue({
      canceled: false,
      assets: [{ uri: 'file:///tmp/camera.mp4' }],
    });
    (api.uploadVideo as jest.Mock).mockResolvedValue({
      data: { upload_id: 'up_1', sha256: 'abc', size_bytes: 1000, duration_seconds: 5, mime_type: 'video/mp4' },
      status: 200,
    });
    (api.submitMediaProof as jest.Mock).mockResolvedValue({
      data: { submission_id: 'sub_1', verification_status: 'pending' },
      status: 202,
    });
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  // -- Web path ---------------------------------------------------------------
  describe('web path (Platform.OS = "web")', () => {
    const originalPlatform = Platform.OS;

    beforeAll(() => {
      (Platform as any).OS = 'web';
    });

    afterAll(() => {
      (Platform as any).OS = originalPlatform;
    });

    it('renders "Choose video file" button', () => {
      const screen = render(<MediaUploader />);
      expect(screen.getByText('Choose video file')).toBeTruthy();
    });

    it('does not render Take Photo or Pick from Library buttons on web', () => {
      const screen = render(<MediaUploader />);
      expect(screen.queryByText('Take Photo')).toBeNull();
      expect(screen.queryByText('Choose from Library')).toBeNull();
    });
  });

  // -- Native idle state ------------------------------------------------------
  describe('native idle state', () => {
    it('renders Take Photo and Choose from Library buttons', () => {
      const screen = render(<MediaUploader />);

      expect(screen.getByTestId('take-photo-button')).toBeTruthy();
      expect(screen.getByText('Take Photo')).toBeTruthy();
      expect(screen.getByTestId('pick-from-library-button')).toBeTruthy();
      expect(screen.getByText('Choose from Library')).toBeTruthy();
    });
  });

  // -- Native: camera capture mode (tap "Take Photo") -------------------------
  describe('native camera capture mode', () => {
    it('opens CameraCapture when Take Photo is pressed', () => {
      const screen = render(<MediaUploader />);

      fireEvent.press(screen.getByText('Take Photo'));

      // CameraCapture should now be rendered (camera preview in the permission-granted case)
      expect(screen.getByTestId('camera-preview')).toBeTruthy();
    });

    it('returns to idle when CameraCapture onCancel is fired (denied permissions)', async () => {
      mockCamera.getPermissions.mockReturnValue(deniedPermissions());

      const onCancel = jest.fn();
      const screen = render(<MediaUploader onCancel={onCancel} />);

      fireEvent.press(screen.getByText('Take Photo'));

      await act(() => {
        jest.advanceTimersByTime(100);
      });

      // CameraCapture renders Cancel in the denied-permission state
      fireEvent.press(screen.getByText('Cancel'));

      expect(onCancel).toHaveBeenCalledTimes(1);
    });
  });

  // -- Native: library pick flow ----------------------------------------------
  describe('native library pick flow', () => {
    it('requests media library permissions when Choose from Library is pressed', async () => {
      const screen = render(<MediaUploader />);

      fireEvent.press(screen.getByText('Choose from Library'));

      await act(() => {
        jest.advanceTimersByTime(100);
      });

      expect(mockImagePicker.requestMediaLibraryPermissionsAsync).toHaveBeenCalledTimes(1);
    });

    it('shows error when media library permission is denied', async () => {
      mockImagePicker.requestMediaLibraryPermissionsAsync.mockResolvedValue(deniedPermissions());

      const screen = render(<MediaUploader />);

      fireEvent.press(screen.getByText('Choose from Library'));

      await act(() => {
        jest.advanceTimersByTime(100);
      });

      expect(screen.getByTestId('upload-error')).toBeTruthy();
      expect(
        screen.getByText('Media library permission denied. Please enable it in Settings to upload a proof video.'),
      ).toBeTruthy();
    });

    it('calls api.uploadVideo when library pick returns an asset', async () => {
      const onUploaded = jest.fn();
      const screen = render(<MediaUploader goalId="goal_1" onUploaded={onUploaded} />);

      fireEvent.press(screen.getByText('Choose from Library'));

      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(api.uploadVideo).toHaveBeenCalledWith(
        { uri: 'file:///tmp/library.mp4', fileName: 'library.mp4', type: 'video/mp4' },
        5,
        'goal_1',
      );
      expect(onUploaded).toHaveBeenCalledWith({
        upload_id: 'up_1',
        sha256: 'abc',
        size_bytes: 1000,
        duration_seconds: 5,
        mime_type: 'video/mp4',
      });
    });

    it('shows error when uploadVideo fails', async () => {
      (api.uploadVideo as jest.Mock).mockResolvedValue({ error: 'Network error' });

      const screen = render(<MediaUploader />);

      fireEvent.press(screen.getByText('Choose from Library'));

      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(screen.getByTestId('upload-error')).toBeTruthy();
      expect(screen.getByText('Network error')).toBeTruthy();
    });

    it('allows Try again after error', async () => {
      (api.uploadVideo as jest.Mock).mockResolvedValue({ error: 'Network error' });

      const screen = render(<MediaUploader />);

      fireEvent.press(screen.getByText('Choose from Library'));

      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(screen.getByText('Try again')).toBeTruthy();

      // Clear mock and retry
      (api.uploadVideo as jest.Mock).mockResolvedValue({
        data: { upload_id: 'up_2', sha256: 'def', size_bytes: 2000, duration_seconds: 10, mime_type: 'video/mp4' },
        status: 200,
      });

      fireEvent.press(screen.getByText('Try again'));

      // Tap "Choose from Library" again
      fireEvent.press(screen.getByText('Choose from Library'));

      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(screen.getByTestId('upload-done')).toBeTruthy();
    });

    it('shows done state with upload ID after successful upload', async () => {
      const screen = render(<MediaUploader />);

      fireEvent.press(screen.getByText('Choose from Library'));

      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(screen.getByTestId('upload-done')).toBeTruthy();
      expect(screen.getByText('up_1')).toBeTruthy();
    });
  });

  // -- Uploading state --------------------------------------------------------
  describe('uploading state', () => {
    it('shows uploading indicator while upload is in progress', async () => {
      let resolveUpload: (value: any) => void;
      const uploadPromise = new Promise<any>((resolve) => {
        resolveUpload = resolve;
      });
      (api.uploadVideo as jest.Mock).mockReturnValue(uploadPromise);

      const screen = render(<MediaUploader />);

      fireEvent.press(screen.getByText('Choose from Library'));

      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(screen.getByText('Uploading video...')).toBeTruthy();

      // Resolve upload
      await act(async () => {
        resolveUpload({
          data: { upload_id: 'up_3', sha256: 'ghi', size_bytes: 3000, duration_seconds: 15, mime_type: 'video/mp4' },
          status: 200,
        });
        await Promise.resolve();
      });

      expect(screen.queryByText('Uploading video...')).toBeNull();
      expect(screen.getByTestId('upload-done')).toBeTruthy();
    });
  });
});