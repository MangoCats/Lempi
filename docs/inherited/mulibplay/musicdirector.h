/**************************************
 *  mulibplay - Music Library Player  *
 *  Copyright (c) 2020 by Mike Inman  *
 * aka MangoCats, all rights reserved *
 *   Free, Open Source, MIT license   *
 **************************************/

#ifndef MUSICDIRECTOR_H
#define MUSICDIRECTOR_H

#include <QObject>
#include <QPointer>
#include "description.h"
#include "playout.h"

class Playout;

class MusicDirector : public QObject
{
    Q_OBJECT
public:
         explicit  MusicDirector( Playout *pop );
             void  restoreSettings();
             void  saveSettings();
             void  breathe();
             void  readStationDescriptions();
             void  selectStationByTime();
             void  setStationDescriptions( QStringList sdl );
      QStringList  getStationDescriptions();
             void  setCoreTracks( QList<qint32>trackIdList );
             void  setCoreTracks( QList<QVariant>trackIdList );
   QList<QVariant> getCoreTracksVar();
             bool  initEligibleArtists();
             bool  initEligibleTracks();
            qreal  occasionWeight( QString occasions );
QMap<qint32,QPair<qint32,qreal> > eligibleRadioCuts();
           qint32  selectTrack( QMultiHash<qint32,qreal> weightedTracks );
     QList<qint32> getSeedTracks();
   static  qint64  rotationValueToSeconds( qreal rv );
   static QString  rotationValueToDesc( qreal rv );
   static QString  timeRemainDesc( qint64 sec );
            qreal  recoveryWeight( qint64 age, qint64 rotSecs, qint64 recSecs );
             void  markCutPlayed( qint32 cutId, qint64 unixTime = 0 );

public slots:
             bool  selectCuts( qint32 );
             void  selectStation( QString );
             void  cutPlayed( qint32 );
             void  initMostRecentPlay();

signals:
             void  cutSelected( qint32 );
             void  takeABreath();

public:
           QPointer<Playout> po;
                Description  de;
QMap<QString,QList<qint32> > stationDescriptions;
                    QString  stationName;
               QList<qint32> coreTracks;
          QMap<qint32,qreal> eligibleArtists;
          QMap<qint32,qreal> eligibleTracks;
        QHash<qint32,qint64> artistsMostRecentPlay;
        QHash<qint32,qint64> tracksMostRecentPlay;
               QList<qint32> suggestedTracks;
                     qint32  exclPoolSz;
                     qint32  randPoolSz;
                     qint32  breatheInterval;
                     qint32  repeatCount;
                     qint32  minLength;
                     qint32  maxLength;
                     qint32  maxDepth;
                     qint32  sampleRate;
                     qint32  maxSeeds;
                      qreal  minWeightLimit;
                      qreal  kidSongWeight;
                       bool  mrpInitialized;
                       bool  selectingCuts;
                       bool  autoSelectStation;
};

class AcousticBrainzValues : public QObject
{
    Q_OBJECT
public:
    explicit  AcousticBrainzValues( QObject *parent = nullptr );
              AcousticBrainzValues( const AcousticBrainzValues &v2 );
              AcousticBrainzValues( qint32 trackId, QObject *parent = nullptr );
       qreal  distSq( const AcousticBrainzValues &v2 );
        void  debugShow();

       qreal  danceable;
       qreal  female;
       qreal  acoustic;
       qreal  aggressive;
       qreal  happy;
       qreal  party;
       qreal  relaxed;
       qreal  sad;
       qreal  bright;
       qreal  tonal;
       qreal  instrumental;
};

#endif // MUSICDIRECTOR_H
