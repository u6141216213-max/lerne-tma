import { create } from 'zustand';
import api from '../services/api';

export const useCollaborativeStore = create((set, get) => ({
  collaborators: [],
  userRole: 'viewer',
  groupProgress: null,
  loading: false,

  fetchCollaborators: async (targetType, targetId) => {
    try {
      const res = await api.get(`/collaborative/${targetType}/${targetId}/collaborators`);
      set({
        collaborators: res.data.collaborators || [],
        userRole: res.data.user_role || 'viewer'
      });
      return res.data;
    } catch (err) {
      console.error('Fetch Collaborators Error:', err);
      throw err;
    }
  },

  addCollaborator: async (targetType, targetId, userIdentifier, role = 'editor', canEditAudio = false) => {
    try {
      const res = await api.post(`/collaborative/${targetType}/${targetId}/add`, {
        user_identifier: userIdentifier,
        role,
        can_edit_audio: canEditAudio
      });
      const { fetchCollaborators } = get();
      await fetchCollaborators(targetType, targetId);
      return res.data;
    } catch (err) {
      console.error('Add Collaborator Error:', err);
      throw err;
    }
  },

  updateCollaboratorRole: async (targetType, targetId, userIdToUpdate, newRole, canEditAudio = false) => {
    try {
      const res = await api.put(`/collaborative/${targetType}/${targetId}/role`, {
        user_id_to_update: userIdToUpdate,
        role: newRole,
        can_edit_audio: canEditAudio
      });
      const { fetchCollaborators } = get();
      await fetchCollaborators(targetType, targetId);
      return res.data;
    } catch (err) {
      console.error('Update Collaborator Role Error:', err);
      throw err;
    }
  },

  removeCollaborator: async (targetType, targetId, userIdToRemove) => {
    try {
      const res = await api.delete(`/collaborative/${targetType}/${targetId}/remove/${userIdToRemove}`);
      const { fetchCollaborators } = get();
      await fetchCollaborators(targetType, targetId);
      return res.data;
    } catch (err) {
      console.error('Remove Collaborator Error:', err);
      throw err;
    }
  },

  removeAllCollaborators: async (targetType, targetId) => {
    try {
      const res = await api.delete(`/collaborative/${targetType}/${targetId}/remove-all`);
      const { fetchCollaborators } = get();
      await fetchCollaborators(targetType, targetId);
      return res.data;
    } catch (err) {
      console.error('Remove All Collaborators Error:', err);
      throw err;
    }
  },

  fetchGroupProgress: async (folderId) => {
    try {
      const res = await api.get(`/collaborative/folder/${folderId}/progress`);
      set({ groupProgress: res.data });
      return res.data;
    } catch (err) {
      console.error('Fetch Group Progress Error:', err);
      throw err;
    }
  }
}));
