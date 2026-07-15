#!/usr/bin/env python3
"""
Script pour extraire le volume précis de chaque région d'un atlas NIfTI.
"""

import numpy as np
import nibabel as nib
import pandas as pd
from pathlib import Path
import argparse


def load_atlas_and_labels(atlas_path, labels_path):
    """
    Charge l'image atlas et les labels associés.
    
    Parameters:
    -----------
    atlas_path : str
        Chemin vers le fichier atlas.nii ou atlas.nii.gz
    labels_path : str
        Chemin vers le fichier atlas.label
        
    Returns:
    --------
    atlas_data : ndarray
        Données de l'atlas (indices des régions)
    affine : ndarray
        Matrice de transformation affine
    labels_dict : dict
        Dictionnaire {id_region: nom_region}
    voxel_volume : float
        Volume d'un voxel en mm³
    """
    
    # Charger l'atlas
    print(f"📂 Chargement de l'atlas: {atlas_path}")
    atlas_img = nib.load(atlas_path)
    atlas_data = atlas_img.get_fdata().astype(int)
    affine = atlas_img.affine
    
    # Calculer le volume d'un voxel (mm³)
    pixdim = atlas_img.header.get_zooms()  # Dimensions en mm
    voxel_volume = pixdim[0] * pixdim[1] * pixdim[2]
    print(f"📏 Taille du voxel: {pixdim[0]:.2f} × {pixdim[1]:.2f} × {pixdim[2]:.2f} mm")
    print(f"📊 Volume par voxel: {voxel_volume:.4f} mm³")
    
    # Charger les labels (format ITK-SnAP)
    print(f"\n📂 Chargement des labels: {labels_path}")
    labels_dict = {}
    
    with open(labels_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # Format ITK-SnAP: IDX  R  G  B  A  VIS MSH  LABEL
            # Exemple: 0   255  0    0  1.00  1  1  Région_1
            parts = line.split(None, 7)  # Split en max 8 colonnes
            
            if len(parts) >= 8:
                try:
                    region_id = int(parts[0])  # IDX
                    region_name = parts[7]      # LABEL (dernière colonne)
                    labels_dict[region_id] = region_name
                except ValueError:
                    continue
    
    print(f"✅ {len(labels_dict)} régions chargées")
    
    return atlas_data, affine, labels_dict, voxel_volume


def calculate_region_volumes(atlas_data, labels_dict, voxel_volume):
    """
    Calcule le volume de chaque région.
    
    Parameters:
    -----------
    atlas_data : ndarray
        Données de l'atlas
    labels_dict : dict
        Dictionnaire des labels
    voxel_volume : float
        Volume d'un voxel en mm³
        
    Returns:
    --------
    results_df : DataFrame
        Tableau avec colonnes: ID, Région, Nombre_voxels, Volume_mm3, Volume_cm3
    """
    
    print("\n🔍 Calcul des volumes...")
    results = []
    
    for region_id in sorted(labels_dict.keys()):
        # Compter les voxels appartenant à cette région
        n_voxels = np.sum(atlas_data == region_id)
        
        if n_voxels > 0:
            volume_mm3 = n_voxels * voxel_volume
            volume_cm3 = volume_mm3 / 1000  # 1 cm³ = 1000 mm³
            
            results.append({
                'ID_Région': region_id,
                'Nom_Région': labels_dict[region_id],
                'Nombre_Voxels': n_voxels,
                'Volume_mm3': volume_mm3,
                'Volume_cm3': volume_cm3
            })
    
    results_df = pd.DataFrame(results)
    
    # Statistiques
    print(f"\n📊 Statistiques des volumes:")
    print(f"   Nombre de régions: {len(results_df)}")
    print(f"   Volume total: {results_df['Volume_mm3'].sum():.2f} mm³ = {results_df['Volume_cm3'].sum():.2f} cm³")
    print(f"   Volume moyen par région: {results_df['Volume_mm3'].mean():.2f} mm³")
    print(f"   Volume min: {results_df['Volume_mm3'].min():.2f} mm³")
    print(f"   Volume max: {results_df['Volume_mm3'].max():.2f} mm³")
    
    return results_df


def save_results(results_df, output_path='atlas_volumes.csv'):
    """
    Sauvegarde les résultats dans un fichier CSV et Excel.
    """
    
    # CSV
    results_df.to_csv(output_path, index=False, sep=',', encoding='utf-8')
    print(f"\n✅ Résultats sauvegardés: {output_path}")
    
    # Excel (si openpyxl est disponible)
    excel_path = output_path.replace('.csv', '.xlsx')
    try:
        results_df.to_excel(excel_path, index=False, sheet_name='Volumes')
        print(f"✅ Résultats Excel: {excel_path}")
    except ImportError:
        print("⚠️  openpyxl non disponible, fichier Excel non créé")
    
    # Afficher les 10 premières régions
    print(f"\n📋 Premiers résultats:")
    print(results_df.head(10).to_string(index=False))


def main():
    """Main"""
    parser = argparse.ArgumentParser(
        description='Extraire le volume de chaque région d\'un atlas NIfTI'
    )
    parser.add_argument('atlas', help='Chemin vers le fichier atlas.nii ou atlas.nii.gz')
    parser.add_argument('labels', help='Chemin vers le fichier atlas.label')
    parser.add_argument('-o', '--output', default='atlas_volumes.csv',
                       help='Fichier de sortie (default: atlas_volumes.csv)')
    
    args = parser.parse_args()
    
    # Vérifier les fichiers
    if not Path(args.atlas).exists():
        print(f"❌ Erreur: {args.atlas} introuvable")
        return
    
    if not Path(args.labels).exists():
        print(f"❌ Erreur: {args.labels} introuvable")
        return
    
    # Traitement
    atlas_data, affine, labels_dict, voxel_volume = load_atlas_and_labels(
        args.atlas, args.labels
    )
    
    results_df = calculate_region_volumes(atlas_data, labels_dict, voxel_volume)
    
    save_results(results_df, args.output)


if __name__ == '__main__':
    main()