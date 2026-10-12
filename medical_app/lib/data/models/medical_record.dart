class MedicalRecord {
  final String id;
  final String title;
  final String source; // 'clinicaltrials' ou 'pubmed'
  final String? nctId;
  final String url;
  final String summary;
  final String? eligibilityCriteria;
  final String phase;
  final String status;
  final String conditions;
  final String sponsor;
  final String locationName;
  final String city;
  final String country;
  final double? latitude;
  final double? longitude;
  final double? distanceKm;
  final String? aiCheatSheet;

  MedicalRecord({
    required this.id,
    required this.title,
    required this.source,
    this.nctId,
    required this.url,
    required this.summary,
    this.eligibilityCriteria,
    required this.phase,
    required this.status,
    required this.conditions,
    required this.sponsor,
    required this.locationName,
    required this.city,
    required this.country,
    this.latitude,
    this.longitude,
    this.distanceKm,
    this.aiCheatSheet,
  });

  factory MedicalRecord.fromJson(Map<String, dynamic> json) {
    return MedicalRecord(
      id: json['id'] ?? '',
      title: json['title'] ?? 'Sans titre',
      source: json['source'] ?? 'Inconnu',
      nctId: json['nct_id'],
      url: json['url'] ?? '',
      summary: json['summary'] ?? '',
      eligibilityCriteria: json['eligibility_criteria'],
      phase: json['phase'] ?? 'N/A',
      status: json['status'] ?? 'UNKNOWN',
      conditions: json['conditions'] ?? '',
      sponsor: json['sponsor'] ?? 'Inconnu',
      locationName: json['location_name'] ?? 'Non spécifié',
      city: json['city'] ?? 'Non spécifié',
      country: json['country'] ?? 'Non spécifié',
      latitude: (json['latitude'] as num?)?.toDouble(),
      longitude: (json['longitude'] as num?)?.toDouble(),
      distanceKm: (json['distance_km'] as num?)?.toDouble(),
      aiCheatSheet: json['ai_cheat_sheet'],
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'title': title,
      'source': source,
      'nct_id': nctId,
      'url': url,
      'summary': summary,
      'eligibility_criteria': eligibilityCriteria,
      'phase': phase,
      'status': status,
      'conditions': conditions,
      'sponsor': sponsor,
      'location_name': locationName,
      'city': city,
      'country': country,
      'latitude': latitude,
      'longitude': longitude,
      'distance_km': distanceKm,
      'ai_cheat_sheet': aiCheatSheet,
    };
  }
}
