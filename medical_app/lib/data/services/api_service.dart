import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

class ApiService {
  String baseUrl = "https://sentinelle.onrender.com";
  String? token;

  ApiService({String? url}) {
    if (url != null && url.isNotEmpty) baseUrl = url;
  }

  Future<void> loadToken() async {
    final prefs = await SharedPreferences.getInstance();
    token = prefs.getString('med_token');
  }

  Future<void> saveSession(String newToken, Map<String, dynamic> user) async {
    token = newToken;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('med_token', newToken);
    await prefs.setString('med_user', jsonEncode(user));
  }

  Future<void> clearSession() async {
    token = null;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('med_token');
    await prefs.remove('med_user');
  }

  Map<String, String> _headers() {
    final h = <String, String>{'Content-Type': 'application/json'};
    if (token != null && token!.isNotEmpty) {
      h['Authorization'] = 'Bearer $token';
    }
    return h;
  }

  Future<Map<String, dynamic>> login({
    required String email,
    required String password,
    required String licenseNumber,
  }) async {
    final uri = Uri.parse("$baseUrl/api/auth/login");
    final response = await http
        .post(uri,
            headers: _headers(),
            body: jsonEncode({
              'email': email,
              'password': password,
              'license_number': licenseNumber,
            }))
        .timeout(const Duration(seconds: 15));
    final data = jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
    if (response.statusCode >= 400) {
      throw Exception(data['error'] ?? 'Login échoué');
    }
    await saveSession(data['token'] as String, Map<String, dynamic>.from(data['user'] as Map));
    return data;
  }

  Future<Map<String, dynamic>> register({
    required String email,
    required String password,
    required String licenseNumber,
    String fullName = '',
  }) async {
    final uri = Uri.parse("$baseUrl/api/auth/register");
    final response = await http
        .post(uri,
            headers: _headers(),
            body: jsonEncode({
              'email': email,
              'password': password,
              'license_number': licenseNumber,
              'full_name': fullName,
            }))
        .timeout(const Duration(seconds: 15));
    final data = jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
    if (response.statusCode >= 400) {
      throw Exception(data['error'] ?? 'Inscription échouée');
    }
    await saveSession(data['token'] as String, Map<String, dynamic>.from(data['user'] as Map));
    return data;
  }

  Future<List<Map<String, dynamic>>> fetchMedicalRecords({String? search, String? source}) async {
    final queryParams = <String, String>{};
    if (search != null && search.isNotEmpty) queryParams['q'] = search;
    if (source != null && source != 'all') queryParams['source'] = source;

    final uri = Uri.parse("$baseUrl/api/medical/search").replace(queryParameters: queryParams);

    try {
      final response = await http.get(uri, headers: _headers()).timeout(const Duration(seconds: 15));
      if (response.statusCode == 200) {
        final decoded = jsonDecode(utf8.decode(response.bodyBytes));
        final List<dynamic> data = decoded is Map ? (decoded['results'] as List? ?? []) : decoded as List;
        return data.cast<Map<String, dynamic>>();
      }
      throw Exception("Erreur serveur : ${response.statusCode}");
    } catch (e) {
      throw Exception("Impossible de se connecter au serveur : $e");
    }
  }

  Future<Map<String, dynamic>> fetchMedicalRecord(String id) async {
    final uri = Uri.parse("$baseUrl/api/medical/$id");
    final response = await http.get(uri, headers: _headers()).timeout(const Duration(seconds: 15));
    if (response.statusCode != 200) {
      throw Exception("Fiche introuvable");
    }
    return jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> contactEmail(String id, {String caseSummary = ''}) async {
    final uri = Uri.parse("$baseUrl/api/medical/$id/contact-email");
    final response = await http
        .post(uri,
            headers: _headers(),
            body: jsonEncode({'case_summary': caseSummary}))
        .timeout(const Duration(seconds: 15));
    if (response.statusCode != 200) {
      throw Exception("Échec mise en relation");
    }
    return jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
  }

  Future<List<Map<String, dynamic>>> fetchNearbyRecords({
    required double lat,
    required double lon,
    double maxKm = 1000.0,
  }) async {
    final uri = Uri.parse("$baseUrl/api/medical/nearby").replace(
      queryParameters: {
        'lat': lat.toString(),
        'lon': lon.toString(),
        'max_km': maxKm.toString(),
      },
    );

    try {
      final response = await http.get(uri, headers: _headers()).timeout(const Duration(seconds: 15));
      if (response.statusCode == 200) {
        final List<dynamic> data = jsonDecode(utf8.decode(response.bodyBytes));
        return data.cast<Map<String, dynamic>>();
      }
      throw Exception("Erreur serveur : ${response.statusCode}");
    } catch (e) {
      throw Exception("Impossible de se connecter au serveur : $e");
    }
  }

  Future<List<Map<String, dynamic>>> fetchPlants({String? search}) async {
    final queryParams = <String, String>{};
    if (search != null && search.isNotEmpty) queryParams['search'] = search;
    final uri = Uri.parse("$baseUrl/api/medical/plants").replace(queryParameters: queryParams);
    final response = await http.get(uri, headers: _headers()).timeout(const Duration(seconds: 20));
    if (response.statusCode != 200) {
      throw Exception("Erreur plantes");
    }
    final List<dynamic> data = jsonDecode(utf8.decode(response.bodyBytes));
    return data.cast<Map<String, dynamic>>();
  }
}
