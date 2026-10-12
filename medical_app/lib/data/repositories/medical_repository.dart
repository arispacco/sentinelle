import 'package:medical_app/data/models/medical_record.dart';
import 'package:medical_app/data/services/api_service.dart';
import 'package:medical_app/data/services/offline_cache.dart';

class MedicalRepository {
  final ApiService _apiService;
  final OfflineCache _cache;

  MedicalRepository({required ApiService apiService, OfflineCache? cache})
      : _apiService = apiService,
        _cache = cache ?? OfflineCache();

  Future<List<MedicalRecord>> searchRecords({String? search, String? source}) async {
    try {
      final rawData = await _apiService.fetchMedicalRecords(search: search, source: source);
      for (final json in rawData) {
        await _cache.putRecord(json);
      }
      return rawData.map((json) => MedicalRecord.fromJson(json)).toList();
    } catch (_) {
      final cached = await _cache.listRecords();
      if (cached.isEmpty) rethrow;
      return cached.map((json) => MedicalRecord.fromJson(json)).toList();
    }
  }

  Future<List<MedicalRecord>> searchNearby({
    required double lat,
    required double lon,
    double maxKm = 1000.0,
  }) async {
    try {
      final rawData = await _apiService.fetchNearbyRecords(lat: lat, lon: lon, maxKm: maxKm);
      for (final json in rawData) {
        await _cache.putRecord(json);
      }
      return rawData.map((json) => MedicalRecord.fromJson(json)).toList();
    } catch (_) {
      final cached = await _cache.listRecords();
      if (cached.isEmpty) rethrow;
      return cached.map((json) => MedicalRecord.fromJson(json)).toList();
    }
  }

  Future<MedicalRecord?> getCached(String id) async {
    final json = await _cache.getRecord(id);
    if (json == null) return null;
    return MedicalRecord.fromJson(json);
  }
}
