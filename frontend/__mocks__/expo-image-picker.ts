import { Platform } from 'react-native';

export const mockImagePicker = {
  requestMediaLibraryPermissionsAsync: jest.fn(),
  getMediaLibraryPermissionsAsync: jest.fn(),
  launchImageLibraryAsync: jest.fn(),
  launchCameraAsync: jest.fn(),
};

export const MediaTypeOptions = {
  All: 'All',
  Images: 'Images',
  Videos: 'Videos',
};

export async function requestMediaLibraryPermissionsAsync() {
  return mockImagePicker.requestMediaLibraryPermissionsAsync();
}

export async function getMediaLibraryPermissionsAsync() {
  return mockImagePicker.getMediaLibraryPermissionsAsync();
}

export async function launchImageLibraryAsync(options: any) {
  return mockImagePicker.launchImageLibraryAsync(options);
}

export async function launchCameraAsync(options: any) {
  return mockImagePicker.launchCameraAsync(options);
}